package ai.holospeed.client;

import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.GameMenuScreen;
import net.minecraft.client.gui.screen.MessageScreen;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.server.integrated.IntegratedServer;
import net.minecraft.text.Text;
import net.minecraft.world.GameMode;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.Set;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/** Normal singleplayer lifecycle; a cancellation never authorizes a late load to start controls. */
final class WorldSessionController {
    interface Port {
        default void observe() {}
        boolean empty();
        boolean local();
        boolean arrived();
        boolean healthy();
        boolean paused();
        boolean stopped();
        void unload();
        void load(String saveName, Runnable aborted);
        void pause();
        void validateSave(String saveName) throws IOException;
        JsonObject metadata();
    }
    private static final long TIMEOUT = 120_000_000_000L;
    private final Port port;
    private final Consumer<JsonObject> reply;
    private final LongSupplier clock;
    private String id, operation, saveName, phase;
    private long deadline, generation;
    private boolean answered, cancelled, aborted, polling;

    WorldSessionController(MinecraftClient client, Consumer<JsonObject> reply) {
        this(new MinecraftPort(client), reply, System::nanoTime);
    }
    WorldSessionController(Port port, Consumer<JsonObject> reply, LongSupplier clock) {
        this.port = port; this.reply = reply; this.clock = clock;
    }
    boolean active() { return phase != null; }

    void receive(JsonObject message, boolean controlsReleased) {
        String requestId = "unknown", requested = "unknown";
        try {
            requestId = GameStateCache.requestId(message);
            if (requestId.isBlank() || requestId.length() > 128) throw new IllegalArgumentException("invalid world request id");
            requested = switch (message.get("type").getAsString()) {
                case "world_unload" -> "unload";
                case "world_load" -> "load";
                case "world_cancel" -> "cancel";
                default -> throw new IllegalArgumentException("unknown world operation");
            };
            Set<String> keys = requested.equals("load") ? Set.of("type", "id", "save_name") : Set.of("type", "id");
            if (!message.keySet().equals(keys)) throw new IllegalArgumentException("invalid world operation fields");
            if (requested.equals("cancel")) {
                cancel("operator cancelled world launch");
                send(requestId, requested, "cancelled", "automatic start cancelled; in-flight world cleanup may still be pending", null, false);
                return;
            }
            if (!controlsReleased) throw new IllegalArgumentException("game controls have not finished cancelling");
            if (active()) throw new IllegalArgumentException("world lifecycle or cancelled-load cleanup is still pending");
            if (!port.empty() && !port.local()) throw new IllegalArgumentException("only local singleplayer worlds may be switched");
            String proposedSave = null;
            if (requested.equals("load")) {
                if (!port.empty() || !port.stopped()) throw new IllegalArgumentException("unload and await stopped server before loading a clone");
                var value = message.get("save_name");
                if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) throw new IllegalArgumentException("save_name must be a string");
                proposedSave = value.getAsString();
                validateName(proposedSave);
                port.validateSave(proposedSave);
            }
            id = requestId; operation = requested; saveName = proposedSave;
            answered = cancelled = aborted = false; generation++;
            deadline = clock.getAsLong() + TIMEOUT;
            phase = requested.equals("load") ? "loading" : "unloading";
            polling = true;
            try {
                if (requested.equals("load")) {
                    long expectedGeneration = generation;
                    port.load(saveName, () -> { if (generation == expectedGeneration) aborted = true; });
                } else port.unload();
            } finally { polling = false; }
            poll();
        } catch (IOException | RuntimeException failure) {
            if (id != null && id.equals(requestId) && active()) {
                cancelled = true;
                answer("failed", "world operation failed: " + failure.getClass().getSimpleName(), false);
            } else send(requestId, requested, "rejected", failure.getMessage(), null, false);
        }
    }

    void cancel(String reason) {
        if (!active()) return;
        cancelled = true;
        answer("cancelled", reason, false);
        // Vanilla resource loading is not forcibly interrupted. Poll waits for
        // its arrival/callback, then follows normal disconnect and save cleanup.
    }

    void poll() {
        if (polling) return;
        port.observe();
        if (!active()) return;
        polling = true;
        try {
            if (clock.getAsLong() >= deadline && !answered) {
                cancelled = true;
                answer("failed", "world operation exceeded120 seconds; controls remain disabled", false);
            }
            if (phase.equals("unloading")) {
                if (port.empty() && port.stopped()) { answer("unloaded", null, false); clear(); }
                return;
            }
            if (aborted && port.empty() && port.stopped()) {
                answer(cancelled ? "cancelled" : "failed", "vanilla world loader returned without a playable world", false);
                clear(); return;
            }
            if (cancelled && port.arrived()) {
                phase = "unloading";
                port.unload();
                return;
            }
            if (!port.healthy()) return;
            if (!port.paused()) { port.pause(); phase = "holding"; return; }
            answer("ready", null, true);
            clear();
        } catch (RuntimeException failure) {
            cancelled = true;
            answer("failed", "world lifecycle failure: " + failure.getClass().getSimpleName(), false);
        } finally { polling = false; }
    }

    private void clear() { phase = null; id = null; operation = null; saveName = null; }
    private void answer(String status, String reason, boolean metadata) {
        if (answered) return;
        answered = true;
        send(id, operation, status, reason, saveName, metadata);
    }
    private void send(String id, String operation, String status, String reason, String save, boolean metadata) {
        JsonObject result = new JsonObject();
        result.addProperty("type", "world_ack"); result.addProperty("id", id);
        result.addProperty("operation", operation); result.addProperty("status", status);
        result.addProperty("reason", reason == null ? null : reason.substring(0, Math.min(300, reason.length())));
        result.addProperty("save_name", save); result.addProperty("controls_armed", false);
        if (metadata) port.metadata().entrySet().forEach(entry -> result.add(entry.getKey(), entry.getValue()));
        reply.accept(result);
    }

    static void validateName(String name) {
        if (name == null || !name.matches("holospeed-demo-[A-Za-z0-9_-]{1,80}"))
            throw new IllegalArgumentException("save_name must name a unique holospeed-demo- clone");
    }
    static void validatePath(Path saves, String name) throws IOException {
        validateName(name);
        Path root = saves.toRealPath(), save = root.resolve(name);
        if (!Files.isDirectory(save, LinkOption.NOFOLLOW_LINKS) || !save.toRealPath().getParent().equals(root)
            || !Files.isRegularFile(save.resolve("level.dat"), LinkOption.NOFOLLOW_LINKS))
            throw new IllegalArgumentException("existing ordinary save folder and level.dat required");
    }

    private static final class MinecraftPort implements Port {
        private final MinecraftClient client;
        private IntegratedServer closingServer;
        MinecraftPort(MinecraftClient client) { this.client = client; observe(); }
        public void observe() { if (client.getServer() != null) closingServer = client.getServer(); }
        public boolean empty() { return client.world == null && client.player == null && client.getServer() == null && client.getNetworkHandler() == null; }
        public boolean local() { return client.getServer() != null && !client.getServer().isRemote(); }
        public boolean arrived() { return client.getServer() != null && client.world != null; }
        public boolean healthy() {
            return local() && client.world != null && client.player != null && client.player.isAlive()
                && client.interactionManager != null && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL
                && (client.currentScreen == null || client.currentScreen instanceof GameMenuScreen);
        }
        public boolean paused() { return client.isPaused() && client.currentScreen instanceof GameMenuScreen; }
        public boolean stopped() { return closingServer == null || !closingServer.getThread().isAlive(); }
        public void unload() {
            if (client.world == null && client.getServer() == null) return;
            if (!local() || client.world == null) throw new IllegalStateException("local world has not finished loading");
            closingServer = client.getServer();
            client.world.disconnect();
            client.disconnect(new MessageScreen(Text.literal("Saving HoloSpeed world")));
            client.setScreen(new TitleScreen());
        }
        public void load(String name, Runnable aborted) { client.createIntegratedServerLoader().start(name, aborted); }
        public void pause() { client.openGameMenu(false); }
        public void validateSave(String name) throws IOException { validatePath(client.getLevelStorage().getSavesDirectory(), name); }
        public JsonObject metadata() {
            JsonObject result = new JsonObject();
            result.addProperty("seed", Long.toString(client.getServer().getSaveProperties().getGeneratorOptions().getSeed()));
            result.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
            result.addProperty("health", client.player.getHealth());
            result.addProperty("inventory_empty", client.player.getInventory().isEmpty());
            result.addProperty("game_mode", client.interactionManager.getCurrentGameMode().getName());
            return result;
        }
    }
}
