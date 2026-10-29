# frozen_string_literal: true

require "json"

module Logquill
  # A hash chain: each record stamped through it carries `meta.prev_hash` and `meta.hash`, so a log can be checked later
  # for an edited or removed line (Logquill.verify_chain). Safe to share between threads.
  class Chain
    # `genesis` is the hash the chain starts from; nil is the standard one.
    def initialize(genesis = nil)
      error = Native.error_slot
      handle = Native.lq_chain_new(Native.c_text(genesis), error)
      raise Error, Native.error_from(error) if handle.null?

      @handle = FFI::AutoPointer.new(handle, Native.method(:lq_chain_free))
    end

    # Chains `record` (a Hash, or a String of JSON text) and returns it as a Hash with the hashes set.
    def stamp(record)
      text = record.is_a?(String) ? record : JsonSafe.dump(record)
      error = Native.error_slot
      out = Native.lq_chain_stamp(@handle, Native.c_text(text), error)
      raise Error, Native.error_from(error) if out.null?

      JSON.parse(Native.take(out))
    end

    # The hash of the last record chained.
    def head
      error = Native.error_slot
      out = Native.lq_chain_head(@handle, error)
      raise Error, Native.error_from(error) if out.null?

      Native.take(out)
    end

    # HMAC-SHA256 of the head under `key` (a String of bytes), hex-encoded: a signature that a deleted last line breaks.
    def sign_head(key)
      bytes = key.to_s.b
      buffer = FFI::MemoryPointer.new(:uint8, [bytes.bytesize, 1].max)
      buffer.put_bytes(0, bytes)
      error = Native.error_slot
      out = Native.lq_chain_sign_head(@handle, buffer, bytes.bytesize, error)
      raise Error, Native.error_from(error) if out.null?

      Native.take(out)
    end
  end
end
