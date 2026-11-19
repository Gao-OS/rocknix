// SPDX-License-Identifier: GPL-2.0
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

var (
	errNoOffer       = errors.New("no update offered")
	errRejectedEvent = errors.New("OTA event rejected")
	tokenPattern     = regexp.MustCompile("^[0-9a-f-]{36}\\.[A-Za-z0-9_-]{43}$")
)

type offer struct {
	ID       string
	URL      *url.URL
	Manifest releaseManifest
}

type client struct {
	api       *url.URL
	token     string
	control   *http.Client
	artifacts *http.Client
	now       func() time.Time
}

type timeoutConn struct {
	net.Conn
}

func (c timeoutConn) Read(p []byte) (int, error) {
	if err := c.Conn.SetReadDeadline(time.Now().Add(40 * time.Second)); err != nil {
		return 0, err
	}
	return c.Conn.Read(p)
}

func newClient(base, tokenFile string) (*client, error) {
	api, err := parseHTTPS(base)
	if err != nil || (api.Path != "" && api.Path != "/") {
		return nil, errors.New("UPDATE_API_URL must be an HTTPS origin")
	}
	data, err := readLocal(tokenFile, 256)
	if err != nil {
		return nil, errors.New("cannot read device token file")
	}
	token := strings.TrimSuffix(string(data), "\n")
	if !tokenPattern.MatchString(token) || !uuidPattern.MatchString(token[:36]) {
		return nil, errors.New("invalid device token")
	}

	control := &http.Client{
		Timeout:       20 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}

	transport := http.DefaultTransport.(*http.Transport).Clone()
	dialer := &net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}
	transport.DialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
		conn, err := dialer.DialContext(ctx, network, addr)
		if err != nil {
			return nil, err
		}
		return timeoutConn{conn}, nil
	}
	transport.ResponseHeaderTimeout = 25 * time.Second
	transport.DisableCompression = true

	artifacts := &http.Client{
		Transport: transport,
		Timeout:   90 * time.Minute,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 3 || req.URL.Scheme != "https" || req.URL.User != nil ||
				!sameOrigin(req.URL, via[0].URL) {
				return errors.New("unsafe artifact redirect")
			}
			return nil
		},
	}

	return &client{api, token, control, artifacts, time.Now}, nil
}

func parseHTTPS(value string) (*url.URL, error) {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil ||
		parsed.Fragment != "" || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Opaque != "" {
		return nil, errors.New("expected an HTTPS URL")
	}
	return parsed, nil
}

func sameOrigin(a, b *url.URL) bool {
	port := func(u *url.URL) string {
		if u.Port() == "" {
			return "443"
		}
		return u.Port()
	}
	return strings.EqualFold(a.Hostname(), b.Hostname()) && port(a) == port(b)
}

func (c *client) controlRequest(ctx context.Context, path string, body []byte) (*http.Response, error) {
	address := c.api.ResolveReference(&url.URL{Path: path})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, address.String(), bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+c.token)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Cache-Control", "no-store")
	result, err := c.control.Do(request)
	if err != nil {
		return nil, errors.New("OTA control-plane request failed")
	}
	return result, nil
}

func (c *client) discover(ctx context.Context, current bootClaim, keys map[string]trustedKey) (offer, error) {
	var result offer
	if !current.valid() {
		return result, errors.New("invalid local boot identity")
	}

	payload, err := json.Marshal(current)
	if err != nil {
		return result, err
	}
	response, err := c.controlRequest(ctx, "/api/v1/discovery", payload)
	if err != nil {
		return result, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return result, fmt.Errorf("OTA discovery returned HTTP %d", response.StatusCode)
	}
	raw, err := readBounded(response.Body, maxMetadata)
	if err != nil {
		return result, err
	}

	var reply discovery
	if err := decodeObject(raw, &reply, "channel", "offer"); err != nil || !validChannel(reply.Channel) {
		return result, errors.New("invalid OTA discovery response")
	}
	if bytes.Equal(reply.Offer, []byte("null")) {
		return result, errNoOffer
	}

	var wire wireOffer
	if err := decodeObject(reply.Offer, &wire, "release_id", "artifact_url", "envelope"); err != nil ||
		!uuidPattern.MatchString(wire.ID) {
		return result, errors.New("invalid OTA offer")
	}

	manifest, err := verifyEnvelope(wire.Envelope, keys, current, reply.Channel, c.now().Unix())
	if err != nil {
		return result, err
	}

	address, err := parseHTTPS(wire.URL)
	if err != nil || sameOrigin(c.api, address) || address.RawPath != "" ||
		!strings.HasSuffix(address.Path, "/sha256/"+manifest.Digest+".squashfs") {
		return result, errors.New("artifact URL does not identify the signed object on a separate HTTPS origin")
	}
	return offer{wire.ID, address, manifest}, nil
}

func (c *client) report(ctx context.Context, data []byte) error {
	response, err := c.controlRequest(ctx, "/api/v1/events", data)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	status := response.StatusCode
	switch status {
	case http.StatusOK, http.StatusCreated:
		return nil
	case http.StatusConflict, http.StatusUnprocessableEntity:
		var reply struct {
			Error string `json:"error"`
		}
		raw, err := readBounded(response.Body, maxMetadata)
		if err == nil && decodeObject(raw, &reply, "error") == nil &&
			((status == http.StatusConflict && reply.Error == "event_conflict") ||
				(status == http.StatusUnprocessableEntity && reply.Error == "invalid_event")) {
			return fmt.Errorf("%w: %s", errRejectedEvent, reply.Error)
		}
	}
	return fmt.Errorf("OTA event returned HTTP %d", status)
}

func (o offer) filename(dir string) string {
	return filepath.Join(dir, "release-"+o.ID+".squashfs")
}

func (c *client) download(ctx context.Context, candidate offer, dir string) error {
	path := candidate.filename(dir)
	if err := checkFile(path, candidate.Manifest); err == nil {
		return nil
	}

	var filesystem syscall.Statfs_t
	if err := syscall.Statfs(dir, &filesystem); err != nil {
		return errors.New("cannot determine update staging space")
	}
	const reserve = 64 * 1024 * 1024
	if uint64(filesystem.Bavail)*uint64(filesystem.Bsize) < uint64(candidate.Manifest.Size)+reserve {
		return errors.New("insufficient staging space for the signed artifact")
	}

	ctx, cancel := context.WithTimeout(ctx, 90*time.Minute)
	defer cancel()
	for attempt := 0; attempt < 3; attempt++ {
		retry, err := c.downloadOnce(ctx, candidate, dir)
		if err == nil || !retry || attempt == 2 {
			return err
		}
		select {
		case <-ctx.Done():
			return errors.New("artifact download timed out")
		case <-time.After(time.Duration(attempt+1) * time.Second):
		}
	}
	return errors.New("artifact download failed")
}

func (c *client) downloadOnce(ctx context.Context, candidate offer, dir string) (bool, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, candidate.URL.String(), nil)
	if err != nil {
		return false, err
	}
	request.Header.Set("Accept-Encoding", "identity")

	response, err := c.artifacts.Do(request)
	if err != nil {
		return true, errors.New("artifact connection failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		retry := response.StatusCode == 408 || response.StatusCode == 429 || response.StatusCode >= 500
		return retry, fmt.Errorf("artifact request returned HTTP %d", response.StatusCode)
	}
	if (response.ContentLength >= 0 && response.ContentLength != candidate.Manifest.Size) ||
		(response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
		return false, errors.New("artifact response conflicts with signed length or encoding")
	}

	temp, err := os.CreateTemp(dir, ".partial-*")
	if err != nil {
		return false, err
	}
	defer os.Remove(temp.Name())
	defer temp.Close()

	digest := sha256.New()
	count, err := io.Copy(io.MultiWriter(temp, digest), io.LimitReader(response.Body, candidate.Manifest.Size+1))
	if err != nil || count < candidate.Manifest.Size {
		return true, errors.New("artifact transfer was interrupted")
	}
	if count != candidate.Manifest.Size || hex.EncodeToString(digest.Sum(nil)) != candidate.Manifest.Digest {
		return false, errors.New("artifact size or SHA-256 does not match signed metadata")
	}

	if err := temp.Sync(); err != nil {
		return false, err
	}
	if err := temp.Close(); err != nil {
		return false, err
	}
	if err := os.Rename(temp.Name(), candidate.filename(dir)); err != nil {
		return false, err
	}
	staging, err := os.Open(dir)
	if err != nil {
		return false, err
	}
	defer staging.Close()

	return false, staging.Sync()
}

func checkFile(path string, manifest releaseManifest) error {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() != manifest.Size {
		return errors.New("local artifact does not match signed size")
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()

	digest := sha256.New()
	n, err := io.Copy(digest, io.LimitReader(file, manifest.Size+1))
	if err != nil || n != manifest.Size || hex.EncodeToString(digest.Sum(nil)) != manifest.Digest {
		return errors.New("local artifact does not match signed SHA-256")
	}
	return nil
}

func readLocal(path string, max int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("expected a regular file")
	}
	return readBounded(f, max)
}

func readBounded(r io.Reader, max int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, max+1))
	if err != nil || int64(len(data)) > max {
		return nil, errors.New("invalid metadata read or size")
	}
	return data, nil
}
