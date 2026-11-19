// SPDX-License-Identifier: GPL-2.0
package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const (
	testID        = "2ee28c78-4373-4bc8-8ace-7c614eca2250"
	testNow int64 = 1784585612
)

func testClaim() bootClaim {
	return bootClaim{board, arch, layout, "20261120.1", 1, strings.Repeat("1", 64), strings.Repeat("2", 64)}
}

func testManifest() releaseManifest {
	return releaseManifest{
		bootClaim:     bootClaim{board, arch, layout, "20261120.2", 2, strings.Repeat("1", 64), strings.Repeat("2", 64)},
		SchemaVersion: 1, Channel: "stable", Digest: strings.Repeat("3", 64), Size: 4_096,
		GitSHA: strings.Repeat("4", 40), IssuedAt: testNow - 10, ExpiresAt: testNow + 500,
		RollbackFrom: []int64{},
	}
}

func testKey(t *testing.T) (trustedKey, ed25519.PrivateKey) {
	t.Helper()
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	keyDigest := sha256.Sum256(public)
	return trustedKey{hex.EncodeToString(keyDigest[:]), base64.RawURLEncoding.EncodeToString(public),
		testNow - 100, testNow + 1000}, private
}

func marshal(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func signed(t *testing.T, manifest []byte, key trustedKey, private ed25519.PrivateKey) []byte {
	t.Helper()
	return marshal(t, envelope{key.KeyID, base64.RawURLEncoding.EncodeToString(manifest),
		base64.RawURLEncoding.EncodeToString(ed25519.Sign(private, manifest))})
}

func TestVerifySignedManifest(t *testing.T) {
	key, private := testKey(t)
	keys := map[string]trustedKey{key.KeyID: key}
	current := testClaim()
	valid := testManifest()
	payload := marshal(t, valid)
	if _, err := verifyEnvelope(signed(t, payload, key, private), keys, current, "stable", testNow); err != nil {
		t.Fatal(err)
	}

	wrongKey, wrongPrivate := testKey(t)
	cases := []struct {
		name   string
		modify func(*releaseManifest)
	}{
		{"wrong board", func(m *releaseManifest) { m.Board = "powkiddy,rgb20sx" }},
		{"wrong architecture", func(m *releaseManifest) { m.Arch = "arm" }},
		{"wrong channel", func(m *releaseManifest) { m.Channel = "beta" }},
		{"wrong boot layout", func(m *releaseManifest) { m.Layout = "unknown" }},
		{"kernel and initramfs", func(m *releaseManifest) { m.Kernel = strings.Repeat("a", 64) }},
		{"wrong DTB", func(m *releaseManifest) { m.DTB = strings.Repeat("0", 64) }},
		{"bad artifact hash", func(m *releaseManifest) { m.Digest = "bad" }},
		{"invalid size", func(m *releaseManifest) { m.Size = maxSize + 1 }},
		{"expired metadata", func(m *releaseManifest) { m.ExpiresAt = testNow }},
		{"future metadata", func(m *releaseManifest) { m.IssuedAt = testNow + 1 }},
		{"expiry beyond key window", func(m *releaseManifest) { m.ExpiresAt = key.NotAfter + 1 }},
		{"minimum source version", func(m *releaseManifest) { m.Sequence = 4; m.MinimumSequence = 3 }},
		{"unsigned downgrade", func(m *releaseManifest) { m.Sequence = current.Sequence }},
		{"unrelated rollback", func(m *releaseManifest) { m.RollbackFrom = []int64{3} }},
		{"duplicate rollback source", func(m *releaseManifest) { m.RollbackFrom = []int64{3, 3} }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			manifest := valid
			tc.modify(&manifest)
			if _, err := verifyEnvelope(signed(t, marshal(t, manifest), key, private), keys, current, "stable", testNow); err == nil {
				t.Fatal("invalid manifest accepted")
			}
		})
	}

	duplicate := append([]byte(`{"version":"duplicate",`), payload[1:]...)
	for name, candidate := range map[string][]byte{
		"unknown key":      signed(t, payload, wrongKey, wrongPrivate),
		"forged key":       signed(t, payload, key, wrongPrivate),
		"duplicate field":  signed(t, duplicate, key, private),
		"trailing content": signed(t, append(payload, []byte("{}")...), key, private),
		"invalid null":     signed(t, bytes.Replace(payload, []byte(`"minimum_sequence":0`), []byte(`"minimum_sequence":null`), 1), key, private),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := verifyEnvelope(candidate, keys, current, "stable", testNow); err == nil {
				t.Fatal("invalid manifest accepted")
			}
		})
	}

	valid.RollbackFrom = []int64{4}
	current.Sequence = 4
	if _, err := verifyEnvelope(signed(t, marshal(t, valid), key, private), keys, current, "stable", testNow); err != nil {
		t.Fatal("explicit signed recovery rejected", err)
	}
	if _, err := verifyEnvelope(signed(t, payload, key, private), keys, testClaim(), "stable", key.NotAfter); err == nil {
		t.Fatal("expired key accepted")
	}
}

func TestDownloadRetriesInterruptedTransferAndPublishesOnlyVerifiedBytes(t *testing.T) {
	data := bytes.Repeat([]byte("firmware"), 512)
	digest := sha256.Sum256(data)
	candidate := offer{ID: testID, Manifest: releaseManifest{Digest: hex.EncodeToString(digest[:]), Size: int64(len(data))}}
	var attempts atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Error("device credential leaked to firmware host")
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		if attempts.Add(1) == 1 {
			_, _ = w.Write(data[:100])
			return
		}
		_, _ = w.Write(data)
	}))
	defer server.Close()
	candidate.URL, _ = url.Parse(server.URL)
	c := client{artifacts: server.Client()}
	dir := t.TempDir()

	if err := c.download(context.Background(), candidate, dir); err != nil {
		t.Fatal(err)
	}
	if attempts.Load() != 2 {
		t.Fatalf("want two attempts, got %d", attempts.Load())
	}
	got, err := os.ReadFile(candidate.filename(dir))
	if err != nil || !bytes.Equal(got, data) {
		t.Fatal("downloaded bytes differ")
	}
	files, err := os.ReadDir(dir)
	if err != nil || len(files) != 1 {
		t.Fatal("interrupted temporary artifact persisted")
	}

	if err := c.download(context.Background(), candidate, dir); err != nil || attempts.Load() != 2 {
		t.Fatal("verified cached artifact wasn't reused")
	}
}

func TestDownloadRejectsWrongHashAndOversizedResponse(t *testing.T) {
	data := bytes.Repeat([]byte("firmware"), 512)
	manifest := releaseManifest{Digest: strings.Repeat("f", 64), Size: int64(len(data))}
	for _, size := range []int{len(data), len(data) + 10} {
		dir := t.TempDir()
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Length", strconv.Itoa(size))
			_, _ = w.Write(bytes.Repeat([]byte{'A'}, size))
		}))
		address, _ := url.Parse(server.URL)
		c := client{artifacts: server.Client()}
		candidate := offer{testID, address, manifest}
		retry, err := c.downloadOnce(context.Background(), candidate, dir)
		server.Close()
		if retry || err == nil {
			t.Fatal("invalid permanent artifact error")
		}
		files, err := os.ReadDir(dir)
		if err != nil || len(files) != 0 {
			t.Fatal("invalid download persisted")
		}
	}
}

func TestLiveDiscoveryRejectsUnsafeArtifactOriginAndHonorsRevocation(t *testing.T) {
	key, private := testKey(t)
	keys := map[string]trustedKey{key.KeyID: key}
	manifest := testManifest()
	encoded := signed(t, marshal(t, manifest), key, private)
	var active atomic.Bool
	active.Store(true)
	var sameOrigin atomic.Bool

	var base string
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/discovery" || !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			t.Error("invalid authenticated discovery request")
		}
		var current bootClaim
		if err := json.NewDecoder(r.Body).Decode(&current); err != nil || current != testClaim() {
			t.Error("wrong compatibility claim")
		}
		offer := json.RawMessage("null")
		if active.Load() {
			host := "https://artifacts.test"
			if sameOrigin.Load() {
				host = base
			}
			offer = marshal(t, wireOffer{testID, host + "/firmware/sha256/" + manifest.Digest + ".squashfs", encoded})
		}
		_ = json.NewEncoder(w).Encode(discovery{"stable", offer})
	}))
	defer server.Close()
	base = server.URL
	token := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(token, []byte(testID+"."+strings.Repeat("a", 43)), 0600); err != nil {
		t.Fatal(err)
	}
	c, err := newClient(base, token)
	if err != nil {
		t.Fatal(err)
	}
	c.control = server.Client()
	c.now = func() time.Time { return time.Unix(testNow, 0) }

	if got, err := c.discover(context.Background(), testClaim(), keys); err != nil || got.ID != testID {
		t.Fatal("valid offer rejected", err)
	}
	sameOrigin.Store(true)
	if _, err := c.discover(context.Background(), testClaim(), keys); err == nil {
		t.Fatal("same-origin artifact accepted")
	}
	active.Store(false)
	if _, err := c.discover(context.Background(), testClaim(), keys); err != errNoOffer {
		t.Fatal("revoked offer was reused", err)
	}
}
