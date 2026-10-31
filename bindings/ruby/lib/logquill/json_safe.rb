# frozen_string_literal: true

require "json"
require "time"

module Logquill
  # Turns any Ruby value into one the JSON encoder accepts, without raising: logging must never crash the app over what it
  # was asked to log. The rules are Python's `default=str` and the other bindings':
  #
  # - Hash keys become strings, Symbols become strings, Times become ISO 8601 (UTC);
  # - NaN and the infinities (which JSON cannot carry) become the strings "NaN", "Infinity", "-Infinity";
  # - an object that is not plain JSON becomes its `to_s`, or "<unprintable Klass>" if that raises;
  # - a String that is not valid UTF-8 (or is binary) keeps its valid characters and shows U+FFFD for the bad bytes;
  # - a cycle, or nesting deeper than DEPTH, becomes "<circular>" / "<too deep>" instead of recursing forever.
  module JsonSafe
    DEPTH = 50

    def self.call(value, depth = 0, seen = {})
      return "<too deep>" if depth > DEPTH

      case value
      when nil, true, false, Integer then value
      when String then utf8(value)
      when Float then finite(value)
      when Symbol then value.to_s
      when Time then value.getutc.iso8601(3)
      when Hash then container(value, seen) { value.each_with_object({}) { |(k, v), out| out[text(k)] = call(v, depth + 1, seen) } }
      when Array then container(value, seen) { value.map { |item| call(item, depth + 1, seen) } }
      else printable(value)
      end
    end

    def self.container(value, seen)
      return "<circular>" if seen[value.object_id]

      seen[value.object_id] = true
      begin
        yield
      ensure
        seen.delete(value.object_id)
      end
    end

    def self.utf8(string)
      return string if string.encoding == Encoding::UTF_8 && string.valid_encoding?

      raw = string.encoding == Encoding::BINARY ? string.dup.force_encoding(Encoding::UTF_8) : string
      raw.encode(Encoding::UTF_8, invalid: :replace, undef: :replace, replace: "\u{FFFD}")
    rescue EncodingError
      string.b.force_encoding(Encoding::UTF_8).scrub("\u{FFFD}")
    end

    def self.finite(float)
      return float if float.finite?

      float.nan? ? "NaN" : (float.positive? ? "Infinity" : "-Infinity")
    end

    def self.text(key)
      key.is_a?(String) ? key : printable(key).to_s
    end

    def self.printable(value)
      value.to_s
    rescue StandardError, NotImplementedError
      "<unprintable #{value.class}>"
    end

    # JSON text for any value.
    def self.dump(value)
      JSON.generate(call(value))
    end
  end
end
