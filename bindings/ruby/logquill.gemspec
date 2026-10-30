# frozen_string_literal: true

require_relative "lib/logquill/version"

# Two shapes of this gem:
#   - the source gem (default): the Ruby code only; it finds the shared library through LOGQUILL_LIB or a build in target/;
#   - a platform gem: set LOGQUILL_PLATFORM (e.g. "arm64-darwin", "x86_64-linux") after copying the built library to
#     lib/logquill/native/ (`rake native`); it carries the library and installs only on that platform.
Gem::Specification.new do |spec|
  spec.name = "logquill"
  spec.version = Logquill::VERSION
  spec.summary = "Structured, agent-aware logging for Ruby, on a Rust core"
  spec.description = "LogQuill for Ruby: the same record shape, redaction, sampling and tamper-evident hash chain as the " \
                     "Python, Node, browser and other bindings, through a C ABI. Redaction runs before anything is written."
  spec.authors = ["Nikhil Arora"]
  spec.homepage = "https://github.com/nikhilvdev/logquill"
  spec.license = "MIT"
  spec.required_ruby_version = ">= 3.1"
  spec.metadata = { "source_code_uri" => spec.homepage, "rubygems_mfa_required" => "true" }

  spec.files = Dir["lib/**/*.rb", "lib/logquill/native/*", "README.md", "LICENSE"]
  spec.require_paths = ["lib"]
  spec.add_dependency "ffi", "~> 1.15"

  platform = ENV.fetch("LOGQUILL_PLATFORM", nil)
  spec.platform = Gem::Platform.new(platform) if platform && !platform.empty?
end
