# Homebrew tap formula stub — rivenai/homebrew-rvn
# usage: brew install rivenai/rvn/rvn
class Rvn < Formula
  desc "Riven CLI — grounded answers with citations, deep research, and Pages"
  homepage "https://rivenai.io"
  version "0.1.0"
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
      https://platform.rivenai.io/console/keys), then:
        rvn chat "who is the CEO of NVIDIA"
    EOS
  end

  test do
    system bin / "rvn", "--version"
  end
end
