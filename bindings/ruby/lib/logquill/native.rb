# frozen_string_literal: true

require "ffi"

module Logquill
  # The C ABI of the Rust core (crates/logquill-ffi, include/logquill.h), loaded with the `ffi` gem. Nothing outside this
  # file touches a pointer: the rest of the gem sees Ruby strings and raises Logquill::Error.
  module Native
    extend FFI::Library

    LIBRARY = case RbConfig::CONFIG["host_os"]
              when /darwin/ then "liblogquill_ffi.dylib"
              when /mswin|mingw|cygwin/ then "logquill_ffi.dll"
              else "liblogquill_ffi.so"
              end

    # Where the shared library is looked for, first match wins: the LOGQUILL_LIB environment variable (a path to the
    # file), a copy shipped inside the gem (precompiled platform gems), then a build in this repository's target/ directory
    # (development). Returns the path, or raises with every place it looked.
    def self.library_path
      tried = []
      from_env = ENV.fetch("LOGQUILL_LIB", nil)
      candidates = []
      candidates << from_env if from_env && !from_env.empty?
      candidates << File.expand_path("native/#{LIBRARY}", __dir__)
      root = File.expand_path("../../../..", __dir__)
      %w[release debug].each { |profile| candidates << File.join(root, "target", profile, LIBRARY) }
      candidates.each do |path|
        return path if File.file?(path)

        tried << path
      end
      raise LoadError,
            "logquill: could not find #{LIBRARY}. Looked in:\n  #{tried.join("\n  ")}\n" \
            "Set LOGQUILL_LIB to the library's path, install a platform build of the gem, or run " \
            "`cargo build -p logquill-ffi --release` in the repository."
    end

    ffi_lib library_path

    attach_function :lq_version, [], :string
    attach_function :lq_abi_version, [], :uint32
    attach_function :lq_string_free, [:pointer], :void
    attach_function :lq_call, %i[string string pointer], :pointer

    attach_function :lq_chain_new, %i[string pointer], :pointer
    attach_function :lq_chain_stamp, %i[pointer string pointer], :pointer
    attach_function :lq_chain_head, %i[pointer pointer], :pointer
    attach_function :lq_chain_sign_head, %i[pointer pointer size_t pointer], :pointer
    attach_function :lq_chain_free, [:pointer], :void

    attach_function :lq_logger_new, %i[string pointer], :pointer
    attach_function :lq_logger_child, %i[pointer string string pointer], :pointer
    attach_function :lq_log, %i[pointer string string string string string pointer pointer], :int32
    attach_function :lq_logger_name, %i[pointer pointer], :pointer
    attach_function :lq_logger_set_level, %i[pointer string pointer], :int32
    attach_function :lq_logger_flush, %i[pointer uint32], :int32
    attach_function :lq_logger_close, %i[pointer uint32], :int32
    attach_function :lq_logger_free, [:pointer], :void

    # The ABI version this gem was written against. A library with a different one has changed a function's meaning.
    ABI_VERSION = 1

    if lq_abi_version != ABI_VERSION
      raise LoadError, "logquill: #{library_path} speaks C ABI #{lq_abi_version}, this gem needs #{ABI_VERSION}"
    end

    # A fresh out-parameter for `char **error`.
    def self.error_slot
      FFI::MemoryPointer.new(:pointer)
    end

    # Reads and frees an owned C string.
    def self.take(pointer)
      text = pointer.read_string.force_encoding(Encoding::UTF_8)
      lq_string_free(pointer)
      text
    end

    # The message a call left in `slot`, freed; a generic one if it left none.
    def self.error_from(slot, fallback = "the call failed")
      pointer = slot.read_pointer
      pointer.null? ? fallback : take(pointer)
    end

    # A Ruby string as the UTF-8, NUL-free text a C string needs. Invalid bytes become U+FFFD and NUL becomes U+2400 rather
    # than raising: a log call must not crash the caller over what it was asked to log.
    def self.c_text(value)
      return nil if value.nil?

      text = value.to_s
      text = text.dup.force_encoding(Encoding::UTF_8) if text.encoding == Encoding::BINARY
      text = text.encode(Encoding::UTF_8, invalid: :replace, undef: :replace, replace: "\u{FFFD}")
      text.include?("\0") ? text.tr("\0", "\u{2400}") : text
    end
  end
end
