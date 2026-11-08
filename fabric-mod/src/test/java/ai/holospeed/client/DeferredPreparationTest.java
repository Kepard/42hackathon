package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import java.util.ArrayDeque;
import java.util.Queue;
import java.util.concurrent.CompletionException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

class DeferredPreparationTest {
    @Test void pollingNeverRunsQueuedPreparationOrWaitsForIt() {
        Queue<Runnable> work = new ArrayDeque<>();
        DeferredPreparation<String> deferred = new DeferredPreparation<>();
        deferred.start(work::add, () -> "lookup");
        assertTrue(deferred.poll().isEmpty());
        assertEquals(1, work.size());
        work.remove().run();
        assertEquals("lookup", deferred.poll().orElseThrow());
        assertTrue(deferred.poll().isEmpty(), "prepared result can only be consumed once");
    }

    @Test void cancellationDiscardsAnAlreadyRunningLateResult() throws Exception {
        DeferredPreparation<String> deferred = new DeferredPreparation<>();
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch complete = new CountDownLatch(1);
        try (var worker = Executors.newSingleThreadExecutor()) {
            deferred.start(worker, () -> {
                started.countDown();
                try { assertTrue(complete.await(2, TimeUnit.SECONDS)); }
                catch (InterruptedException error) { throw new AssertionError(error); }
                return "old-world lookup";
            });
            assertTrue(started.await(2, TimeUnit.SECONDS));
            deferred.cancel();
            complete.countDown();
            worker.submit(() -> {}).get(2, TimeUnit.SECONDS);
            assertTrue(deferred.poll().isEmpty());
        } finally { complete.countDown(); }
    }

    @Test void replacementCannotConsumeAStaleQueuedSkillResult() {
        Queue<Runnable> work = new ArrayDeque<>();
        DeferredPreparation<String> deferred = new DeferredPreparation<>();
        deferred.start(work::add, () -> "old skill");
        deferred.start(work::add, () -> "current skill");
        work.remove().run();
        assertTrue(deferred.poll().isEmpty());
        work.remove().run();
        assertEquals("current skill", deferred.poll().orElseThrow());
    }

    @Test void preparationErrorsSurfaceOnPollWithoutLeavingAPendingAction() {
        Queue<Runnable> work = new ArrayDeque<>();
        DeferredPreparation<String> deferred = new DeferredPreparation<>();
        deferred.start(work::add, () -> { throw new IllegalArgumentException("bad lookup"); });
        work.remove().run();
        CompletionException error = assertThrows(CompletionException.class, deferred::poll);
        assertInstanceOf(IllegalArgumentException.class, error.getCause());
        assertTrue(deferred.poll().isEmpty());
    }
}
