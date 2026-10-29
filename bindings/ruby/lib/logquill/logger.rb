# frozen_string_literal: true

require "json"

module Logquill
  LEVELS = %i[trace debug info warn error fatal].freeze

  # The context bound by Logquill.with_context, merged into every record logged inside the block. Fiber-local: another
  # thread or fiber does not see it, and a thread started inside the block does not inherit it.
  def self.current_context
    (Thread.current[:logquill_context] || []).reduce({}) { |merged, fields| merged.merge(fields) }
  end

  # Binds `fields` to every record logged inside the block, by any logger. The call's own fields win over the context.
  def self.with_context(**fields)
    stack = (Thread.current[:logquill_context] ||= [])
    stack.push(fields.transform_keys(&:to_s))
    yield
  ensure
    stack.pop
  end

  # A logger on the Rust core: level filter, redaction and the hash chain run in Rust before anything is written.
  #
  #   log = Logquill::Logger.new(name: "web", level: :info,
  #                              plugins: [{ type: "redact" }, { type: "pii" }],
  #                              transports: [{ type: "file", path: "web.log" }])
  #   log.info("signed in", user: "alice", password: "hunter2")   # => true; the file has password "***"
  #
  # `transports` takes two kinds of entry. A Hash is a Rust transport (`{type: "console"}` or `{type: "file", path:}`,
  # written on a background thread). An object that responds to `write(line, record)` is a Ruby transport: it is called
  # on the caller's thread with the finished, already-redacted record. The constructor raises ConfigError for a config the
  # core rejects; a log call never raises.
  class Logger
    attr_reader :name

    def initialize(name: "app", level: :info, plugins: [], transports: [], content_policy: nil, tamper_evident: false,
                   queue: nil, return_record: false)
      rust, host = transports.partition { |transport| transport.is_a?(Hash) }
      config = {
        name: name, level: level.to_s, plugins: plugins, transports: rust, tamper_evident: tamper_evident,
        queue: queue, content_policy: content_policy&.to_s
      }.compact
      error = Native.error_slot
      handle = Native.lq_logger_new(JsonSafe.dump(config), error)
      raise ConfigError, Native.error_from(error, "the logger could not be created") if handle.null?

      adopt(handle, name, host, return_record)
    end

    # A logger named "<name>.<suffix>" with `fixed_meta` added to every record. It shares this logger's level and transports.
    def child(suffix, **fixed_meta)
      error = Native.error_slot
      meta = fixed_meta.empty? ? nil : JsonSafe.dump(fixed_meta)
      handle = Native.lq_logger_child(@handle, Native.c_text(suffix), meta, error)
      raise Error, Native.error_from(error) if handle.null?

      other = Logger.allocate
      other.send(:adopt, handle, "#{@name}.#{suffix}", @host, @return_record)
      other
    end

    LEVELS.each do |level|
      # log.info("message", key: value, llm: { model: "..." })  => true when kept, false when filtered; the record when
      # the logger was built with return_record: true.
      define_method(level) { |message = "", llm: nil, **meta| log(level, message, meta, llm) }
    end

    # The method behind the level methods, for a level chosen at run time.
    def log(level, message, meta = {}, llm = nil)
      context = Logquill.current_context
      merged = context.empty? ? meta : context.merge(meta.transform_keys(&:to_s))
      want_record = @return_record || !@host.empty?
      slot = want_record ? FFI::MemoryPointer.new(:pointer) : nil
      error = Native.error_slot
      code = Native.lq_log(
        @handle, level.to_s, nil, Native.c_text(message), merged.empty? ? nil : Native.c_text(JsonSafe.dump(merged)),
        llm.nil? ? nil : Native.c_text(JsonSafe.dump(llm)), slot, error
      )
      return false if code.zero?
      return failed(Native.error_from(error)) if code.negative?

      return true unless want_record

      record = JSON.parse(Native.take(slot.read_pointer))
      deliver(record)
      @return_record ? record : true
    rescue StandardError => e
      failed(e.message)
    end

    # Changes the level for this logger, its parent and its children. Raises ArgumentError for a level that does not exist.
    def set_level(level)
      error = Native.error_slot
      return self unless Native.lq_logger_set_level(@handle, level.to_s, error).negative?

      raise ArgumentError, Native.error_from(error)
    end
    alias level= set_level

    # Waits up to `timeout` seconds for queued records to be written (Rust transports), and flushes Ruby transports that
    # can. true when everything was written in time.
    def flush(timeout: 5.0)
      @host.each { |transport| transport.flush if transport.respond_to?(:flush) }
      Native.lq_logger_flush(@handle, (timeout * 1000).to_i) == 1
    end

    # Flushes, stops the background thread, and closes Ruby transports that respond to `close`. Call once, on shutdown.
    def close(timeout: 5.0)
      done = Native.lq_logger_close(@handle, (timeout * 1000).to_i) == 1
      @host.each { |transport| transport.close if transport.respond_to?(:close) }
      done
    end

    private

    def adopt(handle, name, host, return_record)
      @handle = FFI::AutoPointer.new(handle, Native.method(:lq_logger_free))
      @name = name
      @host = host
      @return_record = return_record
    end

    def deliver(record)
      return if @host.empty?

      line = JSON.generate(record)
      @host.each do |transport|
        transport.write(line, record)
      rescue StandardError => e
        Kernel.warn("logquill: #{transport.class} failed to write a record: #{e.message}")
      end
    end

    def failed(message)
      Kernel.warn("logquill: #{message}")
      false
    end
  end
end
