# frozen_string_literal: true

require "json"

module Logquill
  class Error < StandardError; end

  # Raised by Logger.new for a config the core rejects; the message says which option and what is allowed.
  class ConfigError < Error; end

  # The stateless helpers of the C ABI, named as the Node addon names them. Each is also a snake_case method on Logquill
  # (`Logquill.canonical_json`, `Logquill.verify_chain`, ...). `Logquill.call` runs one by its original name.
  HELPERS = %w[
    canonicalJson formatJson formatText formatLogfmt parseLogfmt parseRecord genesisHash verifyChain signHead
    verifyHeadSignature verifySignedChain redactMeta redactLocal piiRedactMeta piiRedactText applyPolicyValue
    applyContentPolicy contentFields fieldClasses keysInClass toEcsDocument sqlRow createTableSql graphRow cypher
    leafPlaceholder levelFromJson utcTimestamp formatTimestamp buildLlmBlock parseTraceHeader spanIdFor traceIdFor
    traceIdFromParent captureEnv captureContentEnabled semconvResolve epochNs classifyRecord logAttributes exportSpec
    buildTrace
  ].freeze

  # Arguments that are binary keys cross as base64, for these helpers, at these positions.
  KEY_ARGUMENTS = { "signHead" => 1, "verifyHeadSignature" => 1, "verifySignedChain" => 1 }.freeze

  class << self
    # Runs the helper `name` (e.g. "canonicalJson") with `args` and returns the parsed result. A record, `meta` or list of
    # records may be a Hash / Array, or a String holding its JSON text (pass text when a number's spelling matters: `1`
    # and `1.0` hash differently in a tamper-evident chain). Raises Logquill::Error with the core's message.
    def call(name, *args)
      key_at = KEY_ARGUMENTS[name.to_s]
      wire = args.each_with_index.map do |arg, i|
        i == key_at ? [arg.to_s.b].pack("m0") : JsonSafe.call(arg)
      end
      error = Native.error_slot
      out = Native.lq_call(Native.c_text(name), JSON.generate(wire), error)
      raise Error, Native.error_from(error) if out.null?

      JSON.parse(Native.take(out))
    end

    HELPERS.each do |helper|
      snake = helper.gsub(/([A-Z])/) { "_#{Regexp.last_match(1).downcase}" }
      define_method(snake) { |*args| call(helper, *args) }
    end

    # The verification results use Ruby's naming.
    %i[verify_chain verify_signed_chain].each do |method|
      original = instance_method(method)
      define_method(method) do |*args|
        original.bind_call(self, *args).transform_keys { |k| k.gsub(/([A-Z])/) { "_#{Regexp.last_match(1).downcase}" }.to_sym }
      end
    end

    # The library's version, e.g. "3.0.0-dev.0".
    def core_version
      Native.lq_version
    end
  end
end
