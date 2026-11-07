package dev.observatory;

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.function.Supplier;
import javax.imageio.ImageIO;

public final class ViewerEncoder implements AutoCloseable {
    public record Encoded(byte[] jpeg, int width, int height, long nanos) {}

    private final AtomicBoolean busy = new AtomicBoolean();
    private final ExecutorService worker = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "observatory-viewer"); thread.setDaemon(true); return thread;
    });
    private long interval = 1_000_000_000L / 30;
    private long nextFrame;

    public void setFps(int fps) {
        if (fps < 0 || fps > 60) throw new IllegalArgumentException("viewer_fps must be0..60");
        interval = fps == 0 ? 0 : 1_000_000_000L / fps;
        nextFrame = 0;
    }

    public boolean acquire(long now) {
        if (interval == 0 || now < nextFrame || !busy.compareAndSet(false, true)) return false;
        nextFrame = nextFrame == 0 ? now + interval : nextFrame + ((now - nextFrame) / interval + 1) * interval;
        return true;
    }

    public void release() { busy.set(false); }

    public static int rgb(int abgr) {
        return (abgr & 255) << 16 | abgr & 0xff00 | abgr >>> 16 & 255;
    }

    public CompletableFuture<Void> submit(Supplier<BufferedImage> pixels, Consumer<Encoded> send, Consumer<Exception> error) {
        return CompletableFuture.runAsync(() -> {
            try {
                long started = System.nanoTime();
                BufferedImage image = pixels.get();
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                if (!ImageIO.write(image, "jpeg", bytes)) throw new IOException("No JPEG writer");
                send.accept(new Encoded(bytes.toByteArray(), image.getWidth(), image.getHeight(), System.nanoTime() - started));
            } catch (Exception failure) {
                error.accept(failure);
            } finally {
                release();
            }
        }, worker);
    }

    @Override public void close() { worker.shutdownNow(); }
}
