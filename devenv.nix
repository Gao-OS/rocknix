{ pkgs, lib, ... }:

{
  # https://devenv.sh/

  languages.c.enable = true;
  languages.python = {
    enable = true;
    package = pkgs.python3;
  };

  packages = with pkgs; [
    # Core build tools
    bash
    bc
    curl
    gawk
    gperf
    gnumake
    patch
    patchutils
    perl
    rsync
    file
    git
    openssh
    diffutils

    # Compression
    bzip2
    gzip
    xz
    lzop
    zip
    unzip
    zstd

    # C/C++ toolchain (gcc provided by languages.c)
    ncurses.dev

    # Go
    go

    # Java (for build tools)
    jre_headless

    # XML processing
    libxslt     # xsltproc
    xmlstarlet

    # Font utilities
    xorg.mkfontscale
    xorg.mkfontdir
    xorg.bdftopcf

    # Perl modules
    perlPackages.JSON
    perlPackages.ParseYapp
    perlPackages.XMLParser

    # Build utilities
    rdfind
    upx
  ];

  env = {
    LANG = "en_US.UTF-8";
  };

  enterShell = ''
    echo "GaoOS RockNix dev shell"
    echo "  make RK3566          — full device build"
    echo "  make docker-RK3566   — build via Docker"
    echo "  make package PACKAGE=gaoos-ab-boot"
  '';
}
