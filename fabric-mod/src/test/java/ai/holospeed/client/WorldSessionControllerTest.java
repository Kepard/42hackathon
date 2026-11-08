package ai.holospeed.client;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import static org.junit.jupiter.api.Assertions.*;

class WorldSessionControllerTest {
    static final class FakePort implements WorldSessionController.Port {
        boolean empty = true, local = true, healthy, paused, stopped = true;
        int unloads, loads, pauses;
        Runnable aborted;
        public boolean empty() { return empty; }
        public boolean local() { return local; }
        public boolean arrived() { return !empty; }
        public boolean healthy() { return healthy; }
        public boolean paused() { return paused; }
        public boolean stopped() { return stopped; }
        public void unload() { unloads++; empty = true; healthy = false; paused = false; }
        public void load(String name, Runnable callback) { loads++; aborted = callback; }
        public void pause() { pauses++; }
        public void validateSave(String name) {}
        public JsonObject metadata() { JsonObject result = new JsonObject(); result.addProperty("seed", "-7131204740862824481"); return result; }
    }
    static final class Fixture {
        final FakePort port = new FakePort();
        final List<JsonObject> replies = new ArrayList<>();
        final AtomicLong clock = new AtomicLong();
        final WorldSessionController controller = new WorldSessionController(port, replies::add, clock::get);
        void request(String operation, String id) { controller.receive(message(operation, id), true); }
    }
    static JsonObject message(String operation, String id) {
        JsonObject request = new JsonObject(); request.addProperty("type", "world_" + operation); request.addProperty("id", id);
        if (operation.equals("load")) request.addProperty("save_name", "holospeed-demo-abc123");
        return request;
    }

    @Test void unloadAcknowledgesOnlyAfterServerThreadHasFinishedSaving() {
        Fixture f = new Fixture(); f.port.empty = false; f.port.stopped = false;
        f.request("unload", "a");
        assertEquals(1, f.port.unloads); assertTrue(f.replies.isEmpty());
        f.controller.poll(); assertTrue(f.replies.isEmpty());
        f.port.stopped = true; f.controller.poll();
        assertEquals("unloaded", f.replies.getFirst().get("status").getAsString());
        assertFalse(f.controller.active());
    }

    @Test void loadReadinessRequiresHealthyWorldAndActualPauseNotJustRequestedPause() {
        Fixture f = new Fixture(); f.request("load", "a");
        assertEquals(1, f.port.loads); assertTrue(f.replies.isEmpty());
        f.port.empty = false; f.port.healthy = true; f.controller.poll();
        assertEquals(1, f.port.pauses); assertTrue(f.replies.isEmpty());
        f.port.paused = true; f.controller.poll();
        JsonObject reply = f.replies.getFirst();
        assertEquals("ready", reply.get("status").getAsString());
        assertFalse(reply.get("controls_armed").getAsBoolean());
        assertEquals("-7131204740862824481", reply.get("seed").getAsString());
    }

    @Test void cancelAcknowledgesPromptlyButQuarantinesLateVanillaArrivalUntilUnloaded() {
        Fixture f = new Fixture(); f.request("load", "load"); f.request("cancel", "cancel");
        assertEquals(2, f.replies.size());
        assertTrue(f.replies.stream().allMatch(r -> r.get("status").getAsString().equals("cancelled")));
        assertTrue(f.controller.active());
        f.request("unload", "too-soon");
        assertEquals("rejected", f.replies.getLast().get("status").getAsString());
        f.port.empty = false; f.port.healthy = true; f.port.stopped = false; f.controller.poll();
        assertEquals(1, f.port.unloads); assertEquals(0, f.port.pauses);
        f.port.stopped = true; f.controller.poll();
        assertFalse(f.controller.active());
        assertFalse(f.replies.stream().anyMatch(r -> r.get("status").getAsString().equals("ready")));
    }

    @Test void timeoutNeverPermitsLateReadyOrAnotherLoadBeforeCleanup() {
        Fixture f = new Fixture(); f.request("load", "load");
        f.clock.set(121_000_000_000L); f.controller.poll();
        assertEquals("failed", f.replies.getFirst().get("status").getAsString());
        assertTrue(f.controller.active());
        f.port.empty = false; f.port.healthy = true; f.controller.poll(); f.controller.poll();
        assertEquals(1, f.port.unloads); assertFalse(f.controller.active());
        assertEquals(1, f.replies.size());
    }

    @Test void abortedLoaderReturnsFailureAndStaleCallbacksCannotCancelANewGeneration() {
        Fixture f = new Fixture(); f.request("load", "first"); Runnable first = f.port.aborted;
        first.run(); f.controller.poll(); assertFalse(f.controller.active());
        f.request("load", "second"); first.run(); f.controller.poll();
        assertTrue(f.controller.active()); assertEquals(1, f.replies.size());
        f.port.empty = false; f.port.healthy = f.port.paused = true; f.controller.poll();
        assertEquals("ready", f.replies.getLast().get("status").getAsString());
    }

    @Test void rejectsMultiplayerUnreleasedControlsAndUnexpectedFieldsWithoutLoading() {
        Fixture f = new Fixture(); f.port.empty = false; f.port.local = false;
        f.request("unload", "remote"); assertEquals(0, f.port.unloads);
        f.port.empty = true; f.controller.receive(message("load", "busy"), false); assertEquals(0, f.port.loads);
        JsonObject injected = message("load", "path"); injected.addProperty("overwrite", true);
        f.controller.receive(injected, true); assertEquals(0, f.port.loads);
        assertTrue(f.replies.stream().allMatch(r -> r.get("status").getAsString().equals("rejected")));
    }

    @Test void validatesOrdinaryClonePathsWithoutCreatingOrReplacingFiles(@TempDir Path saves) throws Exception {
        Path clone = Files.createDirectory(saves.resolve("holospeed-demo-valid"));
        Files.writeString(clone.resolve("level.dat"), "fixture");
        WorldSessionController.validatePath(saves, "holospeed-demo-valid");
        assertThrows(IllegalArgumentException.class, () -> WorldSessionController.validatePath(saves, "holospeed-demo-missing"));
        for (String invalid : List.of("../world", "holospeed-demo-../outside", "/tmp/world", "world", "holospeed-demo-"))
            assertThrows(IllegalArgumentException.class, () -> WorldSessionController.validateName(invalid));
        Files.createSymbolicLink(saves.resolve("holospeed-demo-link"), clone);
        assertThrows(IllegalArgumentException.class, () -> WorldSessionController.validatePath(saves, "holospeed-demo-link"));
        assertEquals("fixture", Files.readString(clone.resolve("level.dat")));
    }
}
