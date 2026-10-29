package io.logquill;

import com.sun.jna.Pointer;
import com.sun.jna.ptr.PointerByReference;
import java.lang.ref.Cleaner;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * A hash chain: each record stamped through it carries {@code meta.prev_hash} and {@code meta.hash}, so a log can be checked
 * later for an edited or removed line ({@link Core#verifyChain}). Safe to share between threads. Close it, or let it be
 * garbage-collected: the native chain is freed either way.
 */
public final class Chain implements AutoCloseable {
    private static final Cleaner CLEANER = Cleaner.create();

    private record Release(Pointer handle, AtomicBoolean done) implements Runnable {
        @Override
        public void run() {
            if (done.compareAndSet(false, true)) {
                Native.LIB.lq_chain_free(handle);
            }
        }
    }

    private final Pointer handle;
    private final Cleaner.Cleanable cleanable;

    /** Starts a chain from {@code genesis} (null: the standard genesis hash). */
    public Chain(String genesis) {
        PointerByReference error = new PointerByReference();
        Pointer pointer = Native.LIB.lq_chain_new(genesis, error);
        if (pointer == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        this.handle = pointer;
        this.cleanable = CLEANER.register(this, new Release(pointer, new AtomicBoolean()));
    }

    /** Starts a chain from the standard genesis hash. */
    public Chain() {
        this(null);
    }

    /** Chains {@code recordJson} (a record as JSON text) and returns it with the hashes set, as JSON text. */
    public String stamp(String recordJson) {
        PointerByReference error = new PointerByReference();
        Pointer result = Native.LIB.lq_chain_stamp(handle, Json.clean(recordJson), error);
        if (result == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        return Native.take(result);
    }

    /** The hash of the last record chained. */
    public String head() {
        PointerByReference error = new PointerByReference();
        Pointer result = Native.LIB.lq_chain_head(handle, error);
        if (result == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        return Native.take(result);
    }

    /** HMAC-SHA256 of the head under {@code key}, hex-encoded: a signature that a deleted last line breaks. */
    public String signHead(byte[] key) {
        PointerByReference error = new PointerByReference();
        Pointer result = Native.LIB.lq_chain_sign_head(handle, key, key.length, error);
        if (result == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        return Native.take(result);
    }

    /** Frees the native chain. Safe to call twice; the chain must not be used afterwards. */
    @Override
    public void close() {
        cleanable.clean();
    }
}
