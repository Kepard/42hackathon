package ai.holospeed.client;

import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.function.Supplier;

/** Client-thread handoff for background preparation with no action callbacks. */
final class DeferredPreparation<T> {
    private CompletableFuture<T> pending;

    void start(Executor executor, Supplier<T> prepare) {
        cancel();
        pending = CompletableFuture.supplyAsync(() -> Objects.requireNonNull(prepare.get()), executor);
    }

    /** Never waits. Only the owning client thread may consume a prepared value. */
    Optional<T> poll() {
        CompletableFuture<T> ready = pending;
        if (ready == null || !ready.isDone()) return Optional.empty();
        pending = null;
        return Optional.of(ready.getNow(null));
    }

    /** A late worker result is discarded; the worker has no control callback. */
    void cancel() {
        CompletableFuture<T> old = pending;
        pending = null;
        if (old != null) old.cancel(false);
    }
}
