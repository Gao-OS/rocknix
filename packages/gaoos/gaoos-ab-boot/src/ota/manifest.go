// SPDX-License-Identifier: GPL-2.0
package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"slices"
)

const (
	board             = "powkiddy,rgb30"
	arch              = "aarch64"
	layout            = "gaoos-rk3566-ab-v1"
	maxSequence int64 = 9007199254740991
	maxSize     int64 = 4294967296
	maxValidity int64 = 90 * 24 * 60 * 60
	maxMetadata       = 32 * 1024
)

var (
	shaPattern     = regexp.MustCompile("^[0-9a-f]{64}$")
	gitPattern     = regexp.MustCompile("^[0-9a-f]{40}$")
	versionPattern = regexp.MustCompile("^[A-Za-z0-9][A-Za-z0-9._+-]{0,95}$")
	uuidPattern    = regexp.MustCompile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
)

type bootClaim struct {
	Board    string `json:"board"`
	Arch     string `json:"arch"`
	Layout   string `json:"boot_layout"`
	Version  string `json:"version"`
	Sequence int64  `json:"sequence"`
	Kernel   string `json:"kernel_sha256"`
	DTB      string `json:"dtb_sha256"`
}

type releaseManifest struct {
	bootClaim
	SchemaVersion   int     `json:"schema_version"`
	Channel         string  `json:"channel"`
	MinimumSequence int64   `json:"minimum_sequence"`
	Digest          string  `json:"sha256"`
	Size            int64   `json:"size_bytes"`
	GitSHA          string  `json:"git_sha"`
	IssuedAt        int64   `json:"issued_at"`
	ExpiresAt       int64   `json:"expires_at"`
	RollbackFrom    []int64 `json:"rollback_from"`
}

type envelope struct {
	KeyID     string `json:"key_id"`
	Manifest  string `json:"manifest"`
	Signature string `json:"signature"`
}

type trustedKey struct {
	KeyID     string `json:"key_id"`
	PublicKey string `json:"public_key"`
	NotBefore int64  `json:"not_before"`
	NotAfter  int64  `json:"not_after"`
}

type trustFile struct {
	SchemaVersion int               `json:"schema_version"`
	Keys          []json.RawMessage `json:"keys"`
}

type discovery struct {
	Channel string          `json:"channel"`
	Offer   json.RawMessage `json:"offer"`
}

type wireOffer struct {
	ID       string          `json:"release_id"`
	URL      string          `json:"artifact_url"`
	Envelope json.RawMessage `json:"envelope"`
}

func validChannel(channel string) bool {
	return channel == "stable" || channel == "beta" || channel == "nightly"
}

func (current bootClaim) valid() bool {
	return current.Board == board && current.Arch == arch && current.Layout == layout &&
		versionPattern.MatchString(current.Version) && current.Sequence >= 0 && current.Sequence <= maxSequence &&
		shaPattern.MatchString(current.Kernel) && shaPattern.MatchString(current.DTB)
}

// Signed bytes and envelopes need exact field names and unique fields.
// encoding/json accepts case variants and duplicate keys by default.
func decodeObject(data []byte, dest any, fields ...string) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	open, err := decoder.Token()
	if err != nil || open != json.Delim('{') {
		return errors.New("expected a JSON object")
	}

	seen := make(map[string]bool, len(fields))
	for decoder.More() {
		token, err := decoder.Token()
		field, ok := token.(string)
		if err != nil || !ok || !slices.Contains(fields, field) || seen[field] {
			return errors.New("unknown or duplicate JSON field")
		}

		seen[field] = true
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return errors.New("invalid JSON field")
		}
		if bytes.Equal(value, []byte("null")) && field != "offer" {
			return errors.New("unexpected null JSON field")
		}
	}

	close, err := decoder.Token()
	if err != nil || close != json.Delim('}') || len(seen) != len(fields) {
		return errors.New("incomplete JSON object")
	}

	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return errors.New("trailing JSON data")
	}

	if err := json.Unmarshal(data, dest); err != nil {
		return errors.New("invalid JSON value")
	}
	return nil
}

func decodeManifest(data []byte) (releaseManifest, error) {
	var manifest releaseManifest
	err := decodeObject(data, &manifest, "schema_version", "board", "arch", "channel", "version",
		"sequence", "minimum_sequence", "boot_layout", "kernel_sha256", "dtb_sha256", "sha256",
		"size_bytes", "git_sha", "issued_at", "expires_at", "rollback_from")
	if err != nil {
		return manifest, err
	}

	if manifest.SchemaVersion != 1 || !manifest.bootClaim.valid() || manifest.Sequence == 0 ||
		!validChannel(manifest.Channel) || !shaPattern.MatchString(manifest.Digest) ||
		!gitPattern.MatchString(manifest.GitSHA) || manifest.Size < 96 || manifest.Size > maxSize ||
		manifest.MinimumSequence < 0 || manifest.MinimumSequence > maxSequence ||
		manifest.IssuedAt <= 0 || manifest.ExpiresAt <= manifest.IssuedAt ||
		manifest.ExpiresAt-manifest.IssuedAt > maxValidity || len(manifest.RollbackFrom) > 32 {
		return manifest, errors.New("invalid signed manifest fields")
	}

	seen := make(map[int64]bool, len(manifest.RollbackFrom))
	for _, source := range manifest.RollbackFrom {
		if source <= manifest.Sequence || source < manifest.MinimumSequence || source > maxSequence || seen[source] {
			return manifest, errors.New("invalid rollback source sequence")
		}
		seen[source] = true
	}
	if len(manifest.RollbackFrom) == 0 && manifest.MinimumSequence >= manifest.Sequence {
		return manifest, errors.New("invalid upgrade sequence")
	}

	return manifest, nil
}

func base64url(value string) ([]byte, error) {
	result, err := base64.RawURLEncoding.Strict().DecodeString(value)
	if err != nil || base64.RawURLEncoding.EncodeToString(result) != value {
		return nil, errors.New("invalid base64url value")
	}
	return result, nil
}

func decodeTrust(data []byte) (map[string]trustedKey, error) {
	var trust trustFile
	if err := decodeObject(data, &trust, "schema_version", "keys"); err != nil {
		return nil, err
	}
	if trust.SchemaVersion != 1 || len(trust.Keys) == 0 || len(trust.Keys) > 32 {
		return nil, errors.New("invalid trusted-key list")
	}

	keys := make(map[string]trustedKey, len(trust.Keys))
	for _, raw := range trust.Keys {
		var key trustedKey
		if err := decodeObject(raw, &key, "key_id", "public_key", "not_before", "not_after"); err != nil {
			return nil, err
		}

		keyBytes, err := base64url(key.PublicKey)
		digest := sha256.Sum256(keyBytes)
		_, duplicate := keys[key.KeyID]
		if err != nil || len(keyBytes) != ed25519.PublicKeySize || key.KeyID != hex.EncodeToString(digest[:]) ||
			key.NotBefore <= 0 || key.NotAfter <= key.NotBefore || duplicate {
			return nil, errors.New("invalid trusted Ed25519 key")
		}
		keys[key.KeyID] = key
	}
	return keys, nil
}

func verifyEnvelope(raw []byte, keys map[string]trustedKey, current bootClaim, channel string, now int64) (releaseManifest, error) {
	var signed envelope
	var manifest releaseManifest
	if err := decodeObject(raw, &signed, "key_id", "manifest", "signature"); err != nil {
		return manifest, err
	}

	key, ok := keys[signed.KeyID]
	if !ok || now < key.NotBefore || now >= key.NotAfter {
		return manifest, errors.New("unknown or expired signing key")
	}

	payload, err := base64url(signed.Manifest)
	if err != nil || len(signed.Manifest) > 8192 {
		return manifest, errors.New("invalid signed manifest payload")
	}

	sig, err := base64url(signed.Signature)
	public, _ := base64url(key.PublicKey)
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(public, payload, sig) {
		return manifest, errors.New("invalid manifest signature")
	}

	manifest, err = decodeManifest(payload)
	if err != nil {
		return manifest, err
	}
	if manifest.IssuedAt < key.NotBefore || manifest.IssuedAt > now ||
		manifest.ExpiresAt <= now || manifest.ExpiresAt > key.NotAfter {
		return manifest, errors.New("manifest is outside its trusted signing window")
	}

	if !current.valid() || manifest.Channel != channel || manifest.Board != current.Board ||
		manifest.Arch != current.Arch || manifest.Layout != current.Layout || manifest.Kernel != current.Kernel ||
		manifest.DTB != current.DTB || current.Sequence < manifest.MinimumSequence {
		return manifest, errors.New("manifest is incompatible with the running boot components")
	}

	if len(manifest.RollbackFrom) == 0 {
		if manifest.Sequence <= current.Sequence {
			return manifest, errors.New("update sequence does not advance")
		}
	} else if !slices.Contains(manifest.RollbackFrom, current.Sequence) {
		return manifest, fmt.Errorf("recovery does not authorize running sequence %d", current.Sequence)
	}

	return manifest, nil
}
