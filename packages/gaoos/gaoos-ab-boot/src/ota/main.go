// SPDX-License-Identifier: GPL-2.0
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

func run(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return errors.New("expected discover, download, or event")
	}
	command := args[0]
	if command != "discover" && command != "download" && command != "event" {
		return errors.New("unknown OTA helper command")
	}

	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	api := flags.String("api", "", "control-plane HTTPS origin")
	token := flags.String("token-file", "", "device credential file")
	trust := flags.String("trust-file", "", "trusted keys JSON")
	version := flags.String("version", "", "running image version")
	sequence := flags.Int64("sequence", -1, "running image sequence")
	kernel := flags.String("kernel", "", "running kernel SHA-256")
	dtb := flags.String("dtb", "", "running DTB SHA-256")
	dir := flags.String("dir", "", "private staging directory")
	explicitURL := flags.String("url", "", "required offer artifact URL, if supplied")
	explicitDigest := flags.String("sha256", "", "required offer artifact digest, if supplied")
	expectedRelease := flags.String("release-id", "", "discovered release to download")
	eventFile := flags.String("event-file", "", "telemetry JSON file")
	if err := flags.Parse(args[1:]); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected OTA helper arguments")
	}

	client, err := newClient(*api, *token)
	if err != nil {
		return err
	}

	if command == "event" {
		payload, err := readLocal(*eventFile, 2048)
		if err != nil {
			return err
		}
		return client.report(ctx, payload)
	}

	data, err := readLocal(*trust, maxMetadata)
	if err != nil {
		return errors.New("cannot read trusted Ed25519 keys")
	}
	keys, err := decodeTrust(data)
	if err != nil {
		return err
	}

	current := bootClaim{board, arch, layout, *version, *sequence, *kernel, *dtb}
	candidate, err := client.discover(ctx, current, keys)
	if err != nil {
		return err
	}
	manifest := candidate.Manifest
	if command == "download" {
		if (*expectedRelease != "" && *expectedRelease != candidate.ID) ||
			(*explicitURL != "" && *explicitURL != candidate.URL.String()) ||
			(*explicitDigest != "" && *explicitDigest != manifest.Digest) {
			return errors.New("explicit URL or digest does not match the live signed offer")
		}
		if err := client.download(ctx, candidate, *dir); err != nil {
			return err
		}
	}

	fmt.Printf("%s\t%s\t%d\t%d\t%s\n", manifest.Version, manifest.Digest, manifest.Size, manifest.Sequence, candidate.ID)
	return nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	switch err := run(ctx, os.Args[1:]); {
	case err == nil:
	case errors.Is(err, errNoOffer):
		os.Exit(3)
	default:
		fmt.Fprintln(os.Stderr, "gaoos-ota:", err)
		os.Exit(1)
	}
}
