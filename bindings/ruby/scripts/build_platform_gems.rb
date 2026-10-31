# frozen_string_literal: true

# Builds one platform gem per directory of a libs/ tree, for the release workflow:
#
#   ruby scripts/build_platform_gems.rb LIBS_DIR OUT_DIR
#
# LIBS_DIR holds <id>/<library file>, as the workflow's native build uploads them (linux-x64/liblogquill_ffi.so,
# macos-arm64/liblogquill_ffi.dylib, windows-x64/logquill_ffi.dll, ...). Each gem carries only its own library and installs only
# on its platform; `gem push` of each is the publish. Builds happen in a scratch copy, so the working tree gets no native/ directory.
require "fileutils"
require "tmpdir"
require_relative "../lib/logquill/version"

PLATFORMS = {
  "linux-x64" => ["x86_64-linux", "liblogquill_ffi.so"],
  "linux-arm64" => ["aarch64-linux", "liblogquill_ffi.so"],
  "macos-arm64" => ["arm64-darwin", "liblogquill_ffi.dylib"],
  "macos-x64" => ["x86_64-darwin", "liblogquill_ffi.dylib"],
  "windows-x64" => ["x64-mingw-ucrt", "logquill_ffi.dll"]
}.freeze

libs, out = ARGV.map { |path| File.expand_path(path) }
abort "usage: ruby scripts/build_platform_gems.rb LIBS_DIR OUT_DIR" unless libs && out
ids = Dir.children(libs).sort
abort "build_platform_gems: #{libs} holds no platform directories" if ids.empty?
unknown = ids - PLATFORMS.keys
abort "build_platform_gems: no gem platform for #{unknown.join(', ')}; known: #{PLATFORMS.keys.join(', ')}" unless unknown.empty?

root = File.expand_path("..", __dir__)
FileUtils.mkdir_p(out)
Dir.mktmpdir("lq-ruby-gems") do |scratch|
  ids.each do |id|
    platform, library = PLATFORMS.fetch(id)
    source = File.join(libs, id, library)
    abort "build_platform_gems: #{source} is missing; the native build for #{id} did not upload #{library}" unless File.file?(source)

    work = File.join(scratch, id)
    FileUtils.cp_r(root, work)
    FileUtils.mkdir_p(File.join(work, "lib", "logquill", "native"))
    FileUtils.cp(source, File.join(work, "lib", "logquill", "native", library))
    gem_name = "logquill-#{Logquill::VERSION}-#{platform}.gem"
    built = IO.popen({ "LOGQUILL_PLATFORM" => platform }, ["gem", "build", "logquill.gemspec", "--output", gem_name],
                     chdir: work, err: %i[child out], &:read)
    abort "build_platform_gems: gem build for #{id} failed:\n#{built}" unless $?.success?
    FileUtils.cp(File.join(work, gem_name), File.join(out, gem_name))
    puts "built #{gem_name}"
  end
end
