# frozen_string_literal: true

module Logquill
  # Builders for the +plugins:+ option of Logquill::Logger: plugins that run in Rust, in order. A plugin is a plain hash with a
  # +type+, so a hash written by hand works too; these only spell the options out and leave unset ones to the core's defaults.
  module Plugins
    module_function

    # Masks the values of keys (case-insensitively, at every depth); +nil+ keys means password, token, secret, api_key and authorization.
    def redact(keys: nil, classes: nil, replacement: nil)
      build("redact", keys: keys, classes: classes, replacement: replacement)
    end

    # Masks emails, card numbers, phone numbers, SSNs and API keys inside any string of the record's meta.
    def pii(replacement: nil)
      build("pii", replacement: replacement)
    end

    # Keeps about +rate+ (0 to 1) of the records, chosen at random.
    def sampling(rate:)
      build("sampling", rate: rate)
    end

    # Drops records once more than +max_records+ share a key inside +per_seconds+; +key+ is "logger_level" (the default),
    # "logger" or "message".
    def rate_limit(max_records:, per_seconds:, key: nil)
      build("rate_limit", max_records: max_records, per_seconds: per_seconds, key: key)
    end

    # Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes written per window.
    # An option left +nil+ keeps the core's default.
    def adaptive_sampling(base_rate: nil, min_rate: nil, max_rate: nil, keep_at: nil, slow_ms: nil,
                          max_bytes_per_second: nil, window_seconds: nil)
      build("adaptive_sampling", base_rate: base_rate, min_rate: min_rate, max_rate: max_rate, keep_at: keep_at,
                                 slow_ms: slow_ms, max_bytes_per_second: max_bytes_per_second, window_seconds: window_seconds)
    end

    def build(type, **options)
      { type: type }.merge(options.compact)
    end
    private_class_method :build
  end
end
