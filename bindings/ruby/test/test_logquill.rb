# frozen_string_literal: true

require "minitest/autorun"
require "json"
require "tmpdir"
require "logquill"

class HelpersTest < Minitest::Test
  def test_text_helpers_return_strings_and_structured_ones_return_values
    assert_equal '{"a": 2, "b": 1}', Logquill.canonical_json('{"b":1,"a":2}')
    assert_equal "mail ***", Logquill.pii_redact_text("mail alice@example.com")
    assert_equal({ "password" => "***", "n" => 1 }, Logquill.redact_meta({ "password" => "x", "n" => 1 }))
    assert_equal %w[api_key authorization password secret token], Logquill.keys_in_class("secret").sort
  end

  def test_a_number_keeps_its_spelling_when_given_as_json_text
    assert_equal '{"x": 1.0}', Logquill.canonical_json('{"x": 1.0}')
    refute_equal Logquill.canonical_json('{"x": 1.0}'), Logquill.canonical_json('{"x": 1}')
  end

  def test_an_unknown_helper_and_bad_arguments_raise_with_the_cores_message
    assert_includes assert_raises(Logquill::Error) { Logquill.call("noSuchHelper") }.message, "unknown helper"
    assert_includes assert_raises(Logquill::Error) { Logquill.canonical_json("not json") }.message, "not valid JSON"
    assert_includes assert_raises(Logquill::Error) { Logquill.create_table_sql("oracle", "t") }.message, "dialect"
  end

  def test_binary_keys_cross_as_bytes
    key = "\xFF\x00key".b
    head = Logquill.genesis_hash
    signature = Logquill.sign_head(head, key)
    assert Logquill.verify_head_signature(head, key, signature)
    refute Logquill.verify_head_signature(head, "other", signature)
  end

  # The shared corpus group whose inputs are plain strings, read from the repository's schema/ directory.
  def test_the_shared_canonical_json_corpus
    path = File.expand_path("../../../schema/corpus/canonical-json.json", __dir__)
    skip "schema/corpus is not next to this gem (installed copy)" unless File.exist?(path)
    cases = JSON.parse(File.read(path))["cases"]
    refute_empty cases
    failures = cases.reject { |c| Logquill.canonical_json(c["input"]["json"]) == c["expected"]["text"] }
    assert_empty failures.map { |c| c["name"] }
  end
end

class ChainTest < Minitest::Test
  def record(message)
    { "schema_version" => "2.0", "timestamp" => "2026-01-01T00:00:00.000Z", "level" => "INFO", "logger" => "a",
      "message" => message, "meta" => {} }
  end

  def test_stamped_records_verify_and_an_edit_is_found
    chain = Logquill::Chain.new
    records = %w[a b c d].map { |m| chain.stamp(record(m)) }
    verdict = Logquill.verify_chain(records)
    assert_equal [true, 4, chain.head], [verdict[:ok], verdict[:records_checked], verdict[:head_hash]]

    records[2]["message"] = "edited"
    verdict = Logquill.verify_chain(records)
    assert_equal [false, 3], [verdict[:ok], verdict[:broken_at]]
  end

  def test_a_signed_head_catches_a_deleted_last_line
    chain = Logquill::Chain.new
    records = %w[a b c].map { |m| chain.stamp(record(m)) }
    signature = chain.sign_head("secret")
    assert Logquill.verify_signed_chain(records, "secret", signature)[:ok]
    refute Logquill.verify_signed_chain(records.first(2), "secret", signature)[:ok]
    refute Logquill.verify_signed_chain(records, "wrong", signature)[:ok]
  end
end

class JsonSafeTest < Minitest::Test
  def test_values_json_cannot_carry_become_strings_instead_of_raising
    cyclic = { "name" => "loop" }
    cyclic["self"] = cyclic
    unprintable = Class.new { def to_s = raise("no") }.new
    value = Logquill::JsonSafe.call(
      nan: Float::NAN, inf: -Float::INFINITY, sym: :ok, time: Time.utc(2026, 1, 2, 3, 4, 5), cyclic: cyclic, bad: unprintable,
      1 => "int key", nested: [1, 2.5, nil, true]
    )
    assert_equal "NaN", value["nan"]
    assert_equal "-Infinity", value["inf"]
    assert_equal "ok", value["sym"]
    assert_equal "2026-01-02T03:04:05.000Z", value["time"]
    assert_equal "<circular>", value["cyclic"]["self"]
    assert_match(/unprintable/, value["bad"])
    assert_equal "int key", value["1"]
    assert_equal [1, 2.5, nil, true], value["nested"]
    JSON.generate(value) # and it encodes
  end

  def test_very_deep_nesting_stops
    deep = (1..200).reduce("leaf") { |inner, _| { "k" => inner } }
    assert_includes Logquill::JsonSafe.dump(deep), "<too deep>"
  end
end

class LoggerTest < Minitest::Test
  def with_log(config = {})
    Dir.mktmpdir("lq-ruby") do |dir|
      path = File.join(dir, "app.log")
      log = Logquill::Logger.new(**{ plugins: [{ type: "redact" }, { type: "pii" }], transports: [{ type: "file", path: path }] }.merge(config))
      begin
        yield log, path
      ensure
        log.close
      end
    end
  end

  def test_redaction_happens_before_the_file_is_written
    with_log do |log, path|
      assert log.info("signed in", password: "hunter2", note: "mail alice@example.com")
      log.close
      text = File.read(path)
      refute_match(/hunter2|alice@example\.com/, text)
      assert_equal "***", JSON.parse(text.lines.first)["meta"]["password"]
    end
  end

  def test_sampling_rate_limit_and_adaptive_sampling_run_in_the_core
    kept = ->(plugin, message = "m", n: 200, level: :info) do
      log = Logquill::Logger.new(plugins: [plugin])
      Array.new(n) { log.public_send(level, message) }.count(true)
    end
    assert_equal 0, kept.call(Logquill::Plugins.sampling(rate: 0))
    assert_equal 200, kept.call(Logquill::Plugins.sampling(rate: 1))
    assert_equal 3, kept.call(Logquill::Plugins.rate_limit(max_records: 3, per_seconds: 60, key: "message"), n: 10)
    adaptive = Logquill::Plugins.adaptive_sampling(base_rate: 0, min_rate: 0, max_rate: 0)
    assert_equal 0, kept.call(adaptive)
    assert_equal 200, kept.call(adaptive, level: :error)
  end

  def test_a_misspelt_plugin_option_is_rejected_with_the_cores_message
    error = assert_raises(Logquill::Error) { Logquill::Logger.new(plugins: [{ type: "sampling", rte: 0.5 }]) }
    assert_match(/unknown option 'rte'/, error.message)
  end

  def test_the_level_filters_and_a_child_shares_it
    with_log(level: :warn) do |log, _|
      refute log.info("below")
      assert log.error("above")
      child = log.child("db", component: "pg")
      assert_equal "app.db", child.name
      log.set_level(:error)
      refute child.warn("filtered")
    end
  end

  def test_return_record_gives_the_parsed_record_and_children_add_fixed_meta
    with_log(return_record: true) do |log, _|
      record = log.child("db", component: "pg").info("query", ms: 4)
      assert_equal "app.db", record["logger"]
      assert_equal({ "component" => "pg", "ms" => 4 }, record["meta"])
    end
  end

  def test_an_llm_block_is_carried_and_content_is_withheld_by_default
    with_log(return_record: true) do |log, _|
      record = log.info("call", llm: { model: "m", tokens_in: 3 }, input_messages: [{ role: "user", content: "secret prompt" }])
      assert_equal "m", record["llm"]["model"]
      refute_includes JSON.generate(record), "secret prompt"
    end
  end

  def test_a_wrong_config_raises_config_error_naming_the_problem
    assert_includes assert_raises(Logquill::ConfigError) { Logquill::Logger.new(level: :loud) }.message, "Unknown log level"
    assert_includes assert_raises(Logquill::ConfigError) { Logquill::Logger.new(plugins: [{ type: "redactt" }]) }.message, "unknown plugin type"
    assert_includes assert_raises(Logquill::ConfigError) { Logquill::Logger.new(transports: [{ type: "file" }]) }.message, "path"
  end

  def test_a_bad_level_name_raises_argument_error_but_a_bad_value_never_raises
    with_log do |log, _|
      assert_raises(ArgumentError) { log.set_level(:loud) }
      assert log.info("odd values", nan: Float::NAN, obj: Object.new, bytes: "\xFF\xFE".b, nul: "a\0b")
    end
  end

  def test_context_is_merged_into_records_and_does_not_leak
    with_log(return_record: true) do |log, _|
      inside = Logquill.with_context(request_id: "r1", user: "u") { log.info("in", user: "override") }
      assert_equal({ "request_id" => "r1", "user" => "override" }, inside["meta"])
      assert_equal({}, log.info("out")["meta"])
      other = Thread.new { Logquill.with_context(request_id: "t") { Logquill.current_context } }.value
      assert_equal({ "request_id" => "t" }, other)
    end
  end

  class Sink
    attr_reader :lines, :records

    def initialize
      @lines = []
      @records = []
    end

    def write(line, record)
      @lines << line
      @records << record
    end
  end

  def test_a_ruby_transport_receives_the_already_redacted_record
    sink = Sink.new
    log = Logquill::Logger.new(plugins: [{ type: "redact" }], transports: [sink])
    log.info("hi", password: "hunter2")
    assert_equal 1, sink.lines.size
    assert_equal "***", sink.records.first["meta"]["password"]
    refute_includes sink.lines.first, "hunter2"
  end

  def test_a_failing_ruby_transport_does_not_stop_the_others_or_the_caller
    failing = Object.new
    def failing.write(*) = raise("disk full")
    sink = Sink.new
    log = Logquill::Logger.new(transports: [failing, sink])
    _out, err = capture_io { assert log.info("still logged") }
    assert_equal 1, sink.lines.size
    assert_includes err, "disk full"
  end

  def test_the_tamper_chain_is_applied_as_records_are_written
    Dir.mktmpdir("lq-ruby") do |dir|
      path = File.join(dir, "audit.log")
      log = Logquill::Logger.new(tamper_evident: true, transports: [{ type: "file", path: path }])
      10.times { |i| log.info("event #{i}", i: i) }
      log.close
      records = File.readlines(path).map { |line| JSON.parse(line) }
      assert Logquill.verify_chain(records)[:ok]
      records[4]["message"] = "tampered"
      refute Logquill.verify_chain(records)[:ok]
    end
  end

  def test_many_threads_log_through_one_logger
    Dir.mktmpdir("lq-ruby") do |dir|
      path = File.join(dir, "busy.log")
      log = Logquill::Logger.new(tamper_evident: true, transports: [{ type: "file", path: path }])
      8.times.map { |t| Thread.new { 100.times { |i| log.info("t#{t} ##{i}") } } }.each(&:join)
      assert log.close
      records = File.readlines(path).map { |line| JSON.parse(line) }
      assert_equal 800, records.size
      assert Logquill.verify_chain(records)[:ok]
    end
  end

  def test_loggers_that_go_out_of_scope_are_freed_without_trouble
    300.times { |i| Logquill::Logger.new(name: "gc#{i}").child("c").info("x") }
    GC.start
    assert true
  end
end
