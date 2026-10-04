# Homebrew tap formula stub — rivenai/homebrew-rvn
# usage: brew install rivenai/rvn/rvn
#
# STATUS: stub. The referenced release artifacts under dl.rivenai.io/rvn/ do
# not exist yet (verified 404 on 2026-10-04), so this formula will not
# install until the first tagged release publishes binaries and the real
# SHA-256 values replace the placeholders below. Until then, `pip install
# rvn-cli` (see README) is the supported install path.
class Rvn < Formula
  desc "Riven CLI — grounded answers with citations, deep research, and Pages"
  homepage "https://github.com/rivenai/rvn-cli"
  version "0.1.0" # keep in sync with pyproject.toml / src/rvn/__init__.py
  # Replace with the real SHA-256 of the tagged release artifact:
  #   sha256 "..." for the universal2 macOS build under /opt/relay-downloads/rvn/
  # Release feed: https://dl.rivenai.io/latest/rvn/manifest.json
  if OS.mac?
    url "https://dl.rivenai.io/rvn/0.1.0/rvn-macos-universal2"
    sha256 "__PLACEHOLDER_SHA256__"
  else
    url "https://dl.rivenai.io/rvn/0.1.0/rvn-linux-x64"
    sha256 "__PLACEHOLDER_SHA256__"
  end
  license "MIT"

  def install
    bin.install "rvn"
  end

  def caveats
    <<~EOS
      Run `rvn login` once to store your API key (minted at
      https://platform.rivenai.io/keys), then:
        rvn chat "who is the CEO of NVIDIA"
    EOS
  end

  test do
    system bin / "rvn", "--version"
  end
end
