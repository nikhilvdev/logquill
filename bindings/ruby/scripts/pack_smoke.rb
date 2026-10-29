# frozen_string_literal: true

# The gate before publishing: builds the gem as a platform gem carrying the release library, checks what is inside,
# installs it into an empty gem home, and uses it by name from another directory with LOGQUILL_LIB unset, the way a
# consumer does. Nothing is published; everything happens in a scratch directory.
#   cargo build -p logquill-ffi --release && ruby scripts/pack_smoke.rb
require "fileutils"
require "json"
require "rbconfig"
require "tmpdir"

root = File.expand_path("..", __dir__)
repo = File.expand_path("../..", root)
library = case RbConfig::CONFIG["host_os"]
          when /darwin/ then "liblogquill_ffi.dylib"
          when /mswin|mingw|cygwin/ then "logquill_ffi.dll"
          else "liblogquill_ffi.so"
          end
built = File.join(repo, "target", "release", library)
abort "pack_smoke: #{built} is missing; run `cargo build -p logquill-ffi --release` first" unless File.file?(built)

def run(*command, env: {}, chdir: Dir.pwd)
  output = IO.popen(env, command, chdir: chdir, err: %i[child out], &:read)
  abort "pack_smoke: `#{command.join(' ')}` failed:\n#{output}" unless $?.success?
  output
end

Dir.mktmpdir("lq-ruby-pack") do |scratch|
  # build from a copy, so the working tree gets no native/ directory and no .gem file
  work = File.join(scratch, "gem")
  FileUtils.cp_r(root, work)
  FileUtils.mkdir_p(File.join(work, "lib", "logquill", "native"))
  FileUtils.cp(built, File.join(work, "lib", "logquill", "native", library))

  platform = Gem::Platform.local.to_s
  run("gem", "build", "logquill.gemspec", "--output", "logquill.gem", env: { "LOGQUILL_PLATFORM" => platform }, chdir: work)
  packed = File.join(work, "logquill.gem")

  # what is inside: the library, the platform, and nothing that should not ship
  listing = run("sh", "-c", "tar -xOf #{packed} data.tar.gz | tar -tzf -")
  abort "pack_smoke: the gem does not carry lib/logquill/native/#{library}" unless listing.include?("lib/logquill/native/#{library}")
  stray = listing.lines.map(&:strip).reject { |f| f.start_with?("lib/") || f == "README.md" || f.empty? }
  abort "pack_smoke: files that should not ship: #{stray.join(', ')}" unless stray.empty?
  metadata = run("sh", "-c", "tar -xOf #{packed} metadata.gz | gunzip -c")
  abort "pack_smoke: the gem is not a #{platform} platform gem" unless metadata.include?("platform: #{platform}")

  # install into an empty gem home and use it by name
  home = File.join(scratch, "home")
  run("gem", "install", packed, "--install-dir", home, "--no-document", "--ignore-dependencies")
  script = File.join(scratch, "use.rb")
  File.write(script, <<~RUBY)
    require "json"
    require "logquill"
    log = Logquill::Logger.new(plugins: [{ type: "redact" }], return_record: true)
    record = log.info("hello", password: "p", keep: 1)
    puts [record["meta"], Logquill.canonical_json('{"b":1,"a":2}')].to_json
  RUBY
  env = { "GEM_HOME" => home, "GEM_PATH" => [home, *Gem.path].join(File::PATH_SEPARATOR), "LOGQUILL_LIB" => nil }
  out = run("ruby", script, env: env, chdir: scratch).strip
  expected = [{ "password" => "***", "keep" => 1 }, '{"a": 2, "b": 1}'].to_json
  abort "pack_smoke: the installed gem printed #{out.inspect}, wanted #{expected.inspect}" unless out == expected

  puts "pack_smoke ok: #{File.basename(packed)} (#{platform}, #{(File.size(packed) / 1024.0).round} KiB) " \
       "installed into an empty gem home and used by name"
end
