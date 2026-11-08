package ai.holospeed.client;

import ai.holospeed.client.mixin.KeyBindingAccessor;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.screen.GameMenuScreen;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.client.gui.screen.ingame.InventoryScreen;
import net.minecraft.screen.AbstractRecipeScreenHandler;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.InputUtil;
import net.minecraft.client.util.ScreenshotRecorder;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.imageio.ImageIO;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.Base64;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A client-only controls and observations bridge. Game access stays on the client thread. */
public final class HoloSpeedClient implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("holospeed");
    private static volatile Set<String> syntheticKeys = Set.of();
    private static HoloSpeedClient instance;
    private final ExecutorService encoder = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "holospeed-image-encoder"); thread.setDaemon(true); return thread;
    });
    private final Set<String> seenActions = new LinkedHashSet<>();
    private final GameStateCollector gameState = new GameStateCollector();
    private final SkillInputTelemetry skillInputs = new SkillInputTelemetry();
    private MinecraftClient client;
    private BridgeConfig config;
    private BridgeTransport bridge;
    private SkillController skills;
    private KeyBinding emergency;
    private KeyBinding startKey;
    private Map<String, KeyBinding> bindings;
    private JsonObject status = new JsonObject();
    private boolean enabled = true;
    private long tick;
    private long reconnectAt;
    private String pendingObservation;
    private boolean encoding;
    private String frameId;
    private long frameAt;
    private Object frameWorld;
    private String actionId;
    private long actionStart;
    private long actionEnd;
    private long actionDeadline;
    private Object actionWorld;
    private Object skillWorld;
    private long skillDeadline;
    private String pendingArm;
    private long armDeadline;
    private Object armWorld;
    private Object armPlayer;
    private boolean longAttack;

    @Override public void onInitializeClient() {
        client = MinecraftClient.getInstance();
        try { config = BridgeConfig.load(); }
        catch (Exception error) { LOGGER.error("HoloSpeed configuration invalid; bridge disabled: {}", error.getClass().getSimpleName()); return; }
        emergency = KeyBindingHelper.registerKeyBinding(new KeyBinding("key.holospeed.emergency", InputUtil.Type.KEYSYM, GLFW.GLFW_KEY_F8, "category.holospeed"));
        startKey = KeyBindingHelper.registerKeyBinding(new KeyBinding("key.holospeed.start", InputUtil.Type.KEYSYM, GLFW.GLFW_KEY_F9, "category.holospeed"));
        ClientLifecycleEvents.CLIENT_STARTED.register(this::start);
        ClientTickEvents.END_CLIENT_TICK.register(ignored -> { if (bridge != null) onTick(); });
        ClientLifecycleEvents.CLIENT_STOPPING.register(ignored -> { if (bridge != null) { cancel("client shutdown"); bridge.close(); } encoder.shutdownNow(); });
        if (config.token.isBlank()) LOGGER.warn("HoloSpeed needs HOLOSPEED_BRIDGE_TOKEN or token in config/holospeed.json");
    }

    private void start(MinecraftClient readyClient) {
        client = readyClient;
        bindings = Map.ofEntries(
            Map.entry("forward", client.options.forwardKey), Map.entry("back", client.options.backKey),
            Map.entry("left", client.options.leftKey), Map.entry("right", client.options.rightKey),
            Map.entry("jump", client.options.jumpKey), Map.entry("sneak", client.options.sneakKey),
            Map.entry("sprint", client.options.sprintKey), Map.entry("attack", client.options.attackKey),
            Map.entry("use", client.options.useKey), Map.entry("inventory", client.options.inventoryKey),
            Map.entry("drop", client.options.dropKey), Map.entry("swap", client.options.swapHandsKey));
        skills = new SkillController(client);
        bridge = new BridgeTransport(config, message -> client.execute(() -> receive(message)),
            () -> client.execute(() -> { cancel("bridge disconnected"); pendingObservation = null; frameId = null; }));
        instance = this;
    }

    public static void renderTail() {
        HoloSpeedClient self = instance;
        if (self == null) return;
        self.pollArm();
        self.watchdog();
        // Full vanilla frame, including inventory screens, but no HoloSpeed overlay.
        if (self.pendingObservation != null && !self.encoding) self.capture();
        DrawContext context = new DrawContext(self.client, self.client.getBufferBuilders().getEntityVertexConsumers());
        if (self.client.player != null) {
            JsonObject display = self.status.deepCopy();
            if (self.skills.active()) {
                JsonObject skill = self.skills.snapshot();
                display.addProperty("event", "LOCAL " + skill.get("kind").getAsString().toUpperCase(java.util.Locale.ROOT)
                    + " / " + skill.get("phase").getAsString().replace('_', ' '));
            }
            HoloHud.render(context, self.client, display, self.heldInputs(), self.bridge.connected(), self.enabled);
        }
        context.draw();
    }

    public static boolean syntheticModifier(int code) {
        return (code == GLFW.GLFW_KEY_LEFT_SHIFT || code == GLFW.GLFW_KEY_RIGHT_SHIFT) && syntheticKeys.contains("sneak")
            || (code == GLFW.GLFW_KEY_LEFT_CONTROL || code == GLFW.GLFW_KEY_RIGHT_CONTROL) && syntheticKeys.contains("sprint");
    }

    private void onTick() {
        tick++;
        pollArm();
        watchdog();
        boolean skillWasActive = skills.active();
        try { skills.tick(); }
        catch (RuntimeException error) { cancel("skill tick failed"); event("skill_error", error.getClass().getSimpleName()); }
        if (skillWasActive && !skills.active()) frameId = null;
        if (actionId != null && tick >= actionEnd) complete();
        if (!bridge.connected() && System.nanoTime() >= reconnectAt) {
            reconnectAt = System.nanoTime() + 5_000_000_000L;
            bridge.connect();
        }
    }

    public static void operatorKey(int key, int scancode, int action) {
        HoloSpeedClient self = instance;
        if (self == null || action != GLFW.GLFW_PRESS) return;
        // Process events even in GUIs and under low FPS; polling can miss short taps.
        if (self.emergency.matchesKey(key, scancode)) {
            self.enabled = false;
            self.cancel("F8 pressed");
            self.frameId = null;
            self.event("emergency_stop", "F8 pressed");
        } else if (self.startKey.matchesKey(key, scancode)) {
            self.enabled = true;
            self.cancel("F9 start requested");
            self.frameId = null;
            self.event("start_requested", "F9 pressed");
        }
    }

    private void watchdog() {
        if (skills != null && skills.active() && (!enabled || !bridge.connected()
            || (!skills.permitsWorldTransition() && (client.player == null || client.world != skillWorld))
            || (client.player != null && !client.player.isAlive())
            || client.isPaused() || !client.isWindowFocused() || System.nanoTime() > skillDeadline)) {
            cancel(System.nanoTime() > skillDeadline ? "skill wall-clock deadline reached"
                : "skill focus, connection, or world changed");
            frameId = null;
        }
        if (actionId == null) return;
        if (!enabled || !bridge.connected() || client.player == null || client.world != actionWorld
            || !client.player.isAlive() || client.isPaused() || !client.isWindowFocused()
            || (longAttack && client.currentScreen != null)
            || System.nanoTime() > actionDeadline) {
            cancel("input watchdog, focus, or world changed");
        }
    }

    private void receive(JsonObject message) {
        try {
            switch (message.get("type").getAsString()) {
                case "observe" -> {
                    if (pendingObservation != null || encoding || actionId != null) { event("observation_unavailable", "busy"); return; }
                    if (client.player == null || client.world == null) { event("observation_unavailable", "enter a world first"); return; }
                    pendingObservation = message.get("id").getAsString();
                }
                case "action" -> accept(message);
                case "state" -> {
                    JsonObject response = gameState.reply(client, tick, GameStateCache.requestId(message));
                    response.add("skill", skillSnapshot());
                    bridge.send(response);
                }
                case "skill" -> startSkill(message);
                case "arm" -> arm(message);
                case "pause_game" -> {
                    cancel("completed run: pause world");
                    JsonObject response = new JsonObject();
                    response.addProperty("type", "pause_game_ack");
                    response.addProperty("id", GameStateCache.requestId(message));
                    boolean local = client.isInSingleplayer() && client.player != null && client.world != null;
                    if (local) client.openGameMenu(false);
                    gameState.invalidate();
                    response.addProperty("status", local ? "paused" : "unavailable");
                    bridge.send(response);
                }
                case "stop" -> { cancel("controller stop"); pendingObservation = null; frameId = null; }
                case "status" -> status = message.deepCopy();
                default -> event("protocol_error", "unknown message type");
            }
        } catch (RuntimeException error) {
            cancel("invalid command");
            event("protocol_error", "invalid command: " + error.getClass().getSimpleName());
        }
    }

    private void accept(JsonObject message) {
        String id = message.has("id") ? message.get("id").getAsString() : "unknown";
        try {
            if (!enabled) throw new IllegalArgumentException("F8 safety latch is off");
            if (skills.active()) throw new IllegalArgumentException("skill owns game inputs");
            if (actionId != null) throw new IllegalArgumentException("action already running");
            if (seenActions.contains(id)) throw new IllegalArgumentException("duplicate action");
            seenActions.add(id);
            if (seenActions.size() > 4096) seenActions.remove(seenActions.iterator().next());
            if (frameId == null || !frameId.equals(message.get("frame_id").getAsString())) throw new IllegalArgumentException("unknown source frame");
            int ttl = ActionSpec.integer(message, "ttl_ms", 2000, 100, 15000);
            if ((System.nanoTime() - frameAt) / 1_000_000 > ttl) throw new IllegalArgumentException("expired source frame");
            if (client.player == null || client.world != frameWorld || !client.player.isAlive() || client.isPaused() || !client.isWindowFocused()) throw new IllegalArgumentException("world unavailable or not focused");
            ActionSpec action = ActionSpec.parse(message.getAsJsonObject("action"));
            if (action.cursor() != null && client.currentScreen == null) throw new IllegalArgumentException("GUI click requires open screen");
            if (client.currentScreen != null && (action.yaw() != 0 || action.pitch() != 0)) throw new IllegalArgumentException("camera turn requires gameplay view");
            if (client.currentScreen != null && action.duration() > 20) throw new IllegalArgumentException("long attack requires gameplay view");
            if (client.currentScreen != null && action.keys().stream().anyMatch(key -> Set.of("forward", "back", "left", "right", "jump", "attack", "use").contains(key))) throw new IllegalArgumentException("gameplay keys require closed GUI");
            frameId = null;
            release();
            actionId = id;
            actionStart = tick;
            actionEnd = tick + action.duration();
            actionDeadline = System.nanoTime() + (action.duration() * 50L + 500) * 1_000_000;
            actionWorld = client.world;
            longAttack = action.duration() > 20;
            syntheticKeys = action.keys();
            for (String key : action.keys()) {
                KeyBinding binding = bindings.get(key);
                if (binding != null) {
                    InputUtil.Key input = ((KeyBindingAccessor) binding).holospeed$getBoundKey();
                    if (Set.of("inventory", "drop", "swap").contains(key)) {
                        if (client.currentScreen != null) {
                            Screen screen = client.currentScreen;
                            screen.keyPressed(input.getCode(), 0, 0);
                            screen.keyReleased(input.getCode(), 0, 0);
                        } else KeyBinding.onKeyPressed(input);
                    } else {
                        binding.setPressed(true);
                        if (client.currentScreen == null && Set.of("attack", "use").contains(key)) KeyBinding.onKeyPressed(input);
                    }
                }
            }
            if (action.keys().contains("escape")) {
                if (client.currentScreen != null) client.currentScreen.close(); else client.openGameMenu(false);
            }
            if (client.currentScreen == null) {
                client.player.setYaw(client.player.getYaw() + action.yaw());
                client.player.setPitch(Math.max(-90, Math.min(90, client.player.getPitch() + action.pitch())));
                if (action.hotbar() != null) client.player.getInventory().selectedSlot = action.hotbar() - 1;
                if (action.scroll() != 0) client.player.getInventory().scrollInHotbar(action.scroll());
            } else {
                Screen screen = client.currentScreen;
                double x = action.cursor() == null ? screen.width / 2.0 : action.cursor().x() * (screen.width - 1);
                double y = action.cursor() == null ? screen.height / 2.0 : action.cursor().y() * (screen.height - 1);
                if (action.cursor() != null) GLFW.glfwSetCursorPos(client.getWindow().getHandle(), action.cursor().x() * client.getWindow().getWidth(), action.cursor().y() * client.getWindow().getHeight());
                screen.mouseMoved(x, y);
                if (action.cursor() != null) {
                    screen.mouseClicked(x, y, action.cursor().button());
                    screen.mouseReleased(x, y, action.cursor().button());
                }
                if (action.scroll() != 0) screen.mouseScrolled(x, y, 0, action.scroll());
                if (action.hotbar() != null) {
                    InputUtil.Key key = ((KeyBindingAccessor) client.options.hotbarKeys[action.hotbar() - 1]).holospeed$getBoundKey();
                    screen.keyPressed(key.getCode(), 0, 0);
                    screen.keyReleased(key.getCode(), 0, 0);
                }
            }
        } catch (RuntimeException error) {
            if (id.equals(actionId)) cancel("invalid action");
            else ack(id, "rejected", tick, error.getMessage() == null ? "invalid action" : error.getMessage());
        }
    }

    private void startSkill(JsonObject message) {
        String id = message.has("id") && message.get("id").isJsonPrimitive()
            ? message.get("id").getAsString() : "unknown";
        boolean startAttempted = false;
        try {
            if (!enabled) throw new IllegalArgumentException("F8 safety latch is off");
            if (actionId != null || pendingObservation != null || encoding) throw new IllegalArgumentException("game bridge busy");
            if (skills.active()) throw new IllegalArgumentException("skill already running");
            if (client.player == null || client.world == null || !client.player.isAlive()
                || client.isPaused() || !client.isWindowFocused()) throw new IllegalArgumentException("world unavailable or not focused");
            release();
            startAttempted = true;
            JsonObject response = skills.start(message);
            if (skills.active()) {
                skillWorld = client.world;
                int maxTicks = ActionSpec.integer(skills.snapshot(), "max_ticks", 1200, 20, 1800);
                skillDeadline = System.nanoTime() + (maxTicks * 50L + 2000) * 1_000_000;
                frameId = null;
            }
            bridge.send(response);
        } catch (RuntimeException error) {
            if (startAttempted) skills.cancel("skill start failed");
            JsonObject response = new JsonObject();
            response.addProperty("type", "skill_ack");
            response.addProperty("id", id);
            response.addProperty("status", "rejected");
            JsonObject snapshot = skills.snapshot();
            response.add("skill_id", snapshot.get("skill_id"));
            response.add("skill", snapshot);
            String reason = error.getMessage() == null ? "invalid skill" : error.getMessage();
            response.addProperty("reason", reason.substring(0, Math.min(300, reason.length())));
            bridge.send(response);
        }
    }

    private void arm(JsonObject message) {
        String id = "unknown";
        try {
            id = GameStateCache.requestId(message);
            if (id.isBlank() || id.length() > 128) throw new IllegalArgumentException("invalid arm request id");
            cancel("operator start requested");
            enabled = false;
            pendingObservation = null;
            frameId = null;
            String rejection = armRejection();
            if (rejection != null) { armAck(id, "rejected", rejection); return; }
            if (client.currentScreen instanceof GameMenuScreen) client.currentScreen.close();
            else if (client.currentScreen instanceof InventoryScreen || client.currentScreen instanceof CraftingScreen) {
                client.player.closeHandledScreen();
            }
            pendingArm = id;
            armWorld = client.world;
            armPlayer = client.player;
            armDeadline = System.nanoTime() + 2_000_000_000L;
            // Minecraft updates isPaused during its next render pass. Never
            // block this thread waiting for a just-closed pause menu to resume.
            pollArm();
        } catch (RuntimeException error) {
            armAck(id, "rejected", error.getMessage() == null ? "could not arm controls" : error.getMessage());
        }
    }

    private String armRejection() {
        Screen screen = client.currentScreen;
        ArmPolicy.ScreenKind kind = screen == null ? ArmPolicy.ScreenKind.NONE
            : screen instanceof GameMenuScreen ? ArmPolicy.ScreenKind.PAUSE_MENU
            : screen instanceof InventoryScreen || screen instanceof CraftingScreen ? ArmPolicy.ScreenKind.CRAFTING
            : ArmPolicy.ScreenKind.OTHER;
        boolean cursorEmpty = client.player != null && client.player.currentScreenHandler.getCursorStack().isEmpty();
        boolean gridEmpty = true;
        if (client.player != null && client.player.currentScreenHandler instanceof AbstractRecipeScreenHandler<?, ?> handler) {
            for (int slot = 1; slot < handler.getCraftingSlotCount(); slot++) {
                if (!handler.getSlot(slot).getStack().isEmpty()) { gridEmpty = false; break; }
            }
        }
        return ArmPolicy.rejection(client.world != null && client.player != null,
            client.player != null && client.player.isAlive(), client.isWindowFocused(), kind, cursorEmpty, gridEmpty);
    }

    private void pollArm() {
        if (pendingArm == null) return;
        String rejection = armRejection();
        if (client.world != armWorld || client.player != armPlayer) rejection = "world or player changed while resuming";
        if (rejection == null && System.nanoTime() > armDeadline) rejection = "game or input release did not become ready before arm deadline";
        if (rejection != null) { rejectPendingArm(rejection); return; }
        if (client.isPaused() || skills.active()) return;
        String id = pendingArm;
        pendingArm = null;
        enabled = true;
        gameState.invalidate();
        armAck(id, "ready", null);
    }

    private void rejectPendingArm(String reason) {
        String id = pendingArm;
        pendingArm = null;
        if (id != null) armAck(id, "rejected", reason);
    }

    private void armAck(String id, String result, String reason) {
        JsonObject response = new JsonObject();
        response.addProperty("type", "arm_ack");
        response.addProperty("id", id);
        response.addProperty("status", result);
        response.addProperty("reason", reason == null ? null : reason.substring(0, Math.min(300, reason.length())));
        bridge.send(response);
    }

    private void release() {
        for (String key : syntheticKeys) {
            KeyBinding binding = bindings.get(key);
            if (binding != null) {
                binding.setPressed(false);
                ((KeyBindingAccessor) binding).holospeed$setTimesPressed(0);
            }
        }
        syntheticKeys = Set.of();
    }

    private Set<String> heldInputs() {
        Set<String> held = new LinkedHashSet<>(syntheticKeys);
        for (String key : Set.of("forward", "back", "left", "right", "jump", "sneak", "sprint", "attack", "use")) {
            if (bindings.get(key).isPressed()) held.add(key);
        }
        JsonObject skill = skills.snapshot();
        if (skills.active() && (skill.get("kind").getAsString().equals("mine")
            || skill.get("kind").getAsString().equals("portal")
            || skill.get("kind").getAsString().equals("cast_portal")
            || skill.get("phase").getAsString().equals("returning_table"))) {
            Set<String> forced = skillInputs.read();
            if (forced == null) return null;
            held.addAll(forced);
        }
        held.retainAll(Set.of("forward", "back", "left", "right", "jump", "sneak", "sprint", "attack", "use"));
        return Set.copyOf(held);
    }

    private JsonObject skillSnapshot() {
        JsonObject snapshot = skills.snapshot();
        Set<String> held = heldInputs();
        if (held == null) snapshot.add("keys", JsonNull.INSTANCE);
        else {
            JsonArray keys = new JsonArray();
            held.stream().sorted().forEach(keys::add);
            snapshot.add("keys", keys);
        }
        return snapshot;
    }

    private void complete() {
        String id = actionId;
        long start = actionStart;
        release(); actionId = null;
        ack(id, "completed", start, null);
    }

    private void cancel(String reason) {
        String id = actionId;
        long start = actionStart;
        release(); actionId = null;
        rejectPendingArm(reason);
        if (skills != null) skills.cancel(reason);
        if (id != null) ack(id, "cancelled", start, reason);
    }

    private void ack(String id, String result, long start, String reason) {
        JsonObject message = new JsonObject();
        message.addProperty("type", "ack"); message.addProperty("id", id); message.addProperty("status", result);
        message.addProperty("start_tick", start); message.addProperty("end_tick", tick); message.addProperty("reason", reason);
        bridge.send(message);
    }

    private void event(String name, String detail) {
        JsonObject message = new JsonObject();
        message.addProperty("type", "event"); message.addProperty("name", name); message.addProperty("detail", detail);
        bridge.send(message);
    }

    private void capture() {
        String requestId = pendingObservation;
        pendingObservation = null;
        if (client.player == null || client.world == null) { event("observation_unavailable", "world unloaded"); return; }
        encoding = true;
        try (NativeImage nativeImage = ScreenshotRecorder.takeScreenshot(client.getFramebuffer())) {
            int width = nativeImage.getWidth(), height = nativeImage.getHeight();
            int[] pixels = new int[width * height];
            for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
                int color = nativeImage.getColor(x, y);
                pixels[y * width + x] = 0xff000000 | ((color & 0xff) << 16) | (color & 0xff00) | ((color >>> 16) & 0xff);
            }
            frameId = UUID.randomUUID().toString(); frameAt = System.nanoTime(); frameWorld = client.world;
            String capturedId = frameId;
            long capturedTick = tick;
            boolean screenOpen = client.currentScreen != null;
            encoder.execute(() -> encode(requestId, capturedId, capturedTick, screenOpen, width, height, pixels));
        } catch (Exception error) {
            encoding = false; frameId = null;
            event("observation_unavailable", "framebuffer capture failed");
        }
    }

    private void encode(String requestId, String capturedId, long capturedTick, boolean screenOpen, int width, int height, int[] pixels) {
        try {
            BufferedImage original = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
            original.setRGB(0, 0, width, height, pixels, 0, width);
            int targetWidth = Math.min(width, config.captureWidth);
            int targetHeight = Math.max(1, Math.round(height * targetWidth / (float) width));
            BufferedImage scaled = new BufferedImage(targetWidth, targetHeight, BufferedImage.TYPE_INT_RGB);
            Graphics2D graphics = scaled.createGraphics();
            graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR);
            graphics.drawImage(original, 0, 0, targetWidth, targetHeight, null); graphics.dispose();
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ImageIO.write(scaled, "png", output);
            JsonObject message = new JsonObject();
            message.addProperty("type", "observation"); message.addProperty("id", requestId);
            message.addProperty("frame_id", capturedId); message.addProperty("tick", capturedTick);
            message.addProperty("width", targetWidth); message.addProperty("height", targetHeight);
            message.addProperty("image_base64", Base64.getEncoder().encodeToString(output.toByteArray()));
            message.addProperty("mime_type", "image/png"); message.addProperty("screen_open", screenOpen);
            client.execute(() -> { if (capturedId.equals(frameId)) bridge.send(message); encoding = false; });
        } catch (Exception error) {
            client.execute(() -> { encoding = false; frameId = null; event("observation_unavailable", "image encoding failed"); });
        }
    }
}
