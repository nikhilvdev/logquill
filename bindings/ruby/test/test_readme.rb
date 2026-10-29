# frozen_string_literal: true

require "minitest/autorun"
require "json"
require "open3"
require "rbconfig"
require "tmpdir"

# The README's example is a file that is run here, and the README must contain it word for word, so what the README
# shows is code that works.
class ReadmeTest < Minitest::Test
  ROOT = File.expand_path("..", __dir__)

  def test_the_readme_contains_the_quickstart_verbatim
    example = File.read(File.join(ROOT, "examples", "quickstart.rb")).strip
    assert_includes File.read(File.join(ROOT, "README.md")), "```ruby\n#{example}\n```"
  end

  def test_the_quickstart_runs_and_writes_a_redacted_log
    Dir.mktmpdir("lq-readme") do |dir|
      _out, err, status = Open3.capture3(RbConfig.ruby, "-I", File.join(ROOT, "lib"), File.join(ROOT, "examples", "quickstart.rb"), chdir: dir)
      assert status.success?, err
      lines = File.readlines(File.join(dir, "web.log")).map { |line| JSON.parse(line) }
      assert_equal 2, lines.size
      assert_equal "***", lines[0]["meta"]["password"]
      assert_equal "***", lines[0]["meta"]["user"]
      assert_equal "web.db", lines[1]["logger"]
      assert_equal({ "component" => "pg", "ms" => 840 }, lines[1]["meta"])
    end
  end
end
