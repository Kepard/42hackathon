package dev.observatory;

import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import javax.imageio.ImageIO;

public class ViewerEncoderTest {
    public static void main(String[] args) throws Exception {
        try (ViewerEncoder viewer = new ViewerEncoder()) {
            check(viewer.acquire(0));
            check(!viewer.acquire(100_000_000L));
            viewer.release();
            check(!viewer.acquire(20_000_000L));
            check(viewer.acquire(34_000_000L)); viewer.release();
            check(viewer.acquire(67_000_000L)); viewer.release();
            viewer.setFps(0); check(!viewer.acquire(1_000_000_000L));
            check(viewer.stateDue(0)); check(!viewer.stateDue(500_000_000L));
            check(viewer.stateDue(1_000_000_000L));
            boolean rejected = false;
            try { viewer.setFps(-1); } catch (IllegalArgumentException expected) { rejected = true; }
            check(rejected);
            viewer.setFps(30);
            check(ViewerEncoder.rgb(0xff332211) == 0x112233);
            BufferedImage image = new BufferedImage(8, 8, BufferedImage.TYPE_INT_RGB);
            for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) image.setRGB(x, y, ViewerEncoder.rgb(0xff0000ff));
            AtomicInteger delivered = new AtomicInteger(), failed = new AtomicInteger();
            check(viewer.acquire(2_000_000_000L));
            viewer.submit(() -> {
                check(Thread.currentThread().getName().equals("observatory-viewer")); return image;
            }, result -> {
                try {
                    BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(result.jpeg()));
                    check(decoded.getWidth() == 8 && decoded.getHeight() == 8);
                    int pixel = decoded.getRGB(0, 0);
                    check((pixel >> 16 & 255) > 240 && (pixel & 255) < 15);
                    check(result.nanos() > 0); delivered.incrementAndGet();
                } catch (Exception error) { throw new RuntimeException(error); }
            }, error -> failed.incrementAndGet()).get(3, TimeUnit.SECONDS);
            check(delivered.get() == 1 && failed.get() == 0);
            check(viewer.acquire(3_000_000_000L));
            viewer.submit(() -> null, result -> delivered.incrementAndGet(), error -> failed.incrementAndGet()).get(3, TimeUnit.SECONDS);
            check(failed.get() == 1 && delivered.get() == 1);
            check(viewer.acquire(4_000_000_000L)); viewer.release();
        }
        System.out.println("PASS viewer:30FPS scheduling, no encoding backlog, RGB JPEG, disabled stream, invalid FPS and failure release");
    }

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }
}
