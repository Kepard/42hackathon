package dev.observatory;

import com.google.gson.*;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.event.player.UseItemCallback;
import net.minecraft.util.TypedActionResult;
import net.minecraft.block.Blocks;
import net.minecraft.item.Items;
import net.minecraft.util.ActionResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.InputUtil;
import net.minecraft.client.util.ScreenshotRecorder;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Item;
import net.minecraft.registry.Registries;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.resource.DataConfiguration;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.MinecraftServer;
import net.minecraft.stat.Stats;
import net.minecraft.util.Identifier;
import net.minecraft.world.Difficulty;
import net.minecraft.world.GameMode;
import net.minecraft.world.GameRules;
import net.minecraft.world.gen.GeneratorOptions;
import net.minecraft.world.gen.WorldPresets;
import net.minecraft.world.level.LevelInfo;
import org.java_websocket.WebSocket;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;
import org.lwjgl.glfw.GLFW;

import java.net.InetSocketAddress;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.awt.image.BufferedImage;
import javax.imageio.ImageIO;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

public class Observatory implements ClientModInitializer {
    private static JsonObject overlay = new JsonObject();
    private static boolean overlayDrawn;
    private static long objectiveUntil;
    private static long completionUntil;
    private static boolean autoJump = true;
    private static int renderDistance = 2;
    private static int windowWidth = 854;

    public static void afterHud(DrawContext context) {
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.currentScreen == null && !captures.isEmpty()) {
            context.draw();
            Capture pending;
            while ((pending = captures.poll()) != null) capture(pending, "after_hud_before_overlay");
        }
        if (overlay.size() == 0 || (mc.currentScreen != null && !captures.isEmpty())) return;
        overlayDrawn = true;
        int width = mc.getWindow().getScaledWidth() / 2 - 6;
        context.fill(6, 6, width + 6, 30, 0xB0101A29);
        int complete = Math.max(0, Math.min(13, overlay.has("completed") ? overlay.get("completed").getAsInt() : 0));
        long now = System.nanoTime();
        boolean flash = now < completionUntil && (now / 200_000_000L) % 2 == 0;
        for (int i = 0; i < 13; i++) {
            int x = 10 + i * 3;
            int color = flash ? 0xFFFFFFFF : i < complete ? 0xFF42D9B0 : i == complete ? 0xFFFFCF66 : 0xFF526077;
            context.fill(x, 11, x + 2, 13, color);
        }
        double seconds = overlay.has("api_latency_ms") ? overlay.get("api_latency_ms").getAsDouble() / 1000 : 0;
        String latency = String.format(Locale.ROOT, "API %.1fs", seconds);
        String badges = Arrays.stream(label("badges", "").split("\\s+"))
                .filter(badge -> badge.equals("PAUSED") || badge.equals("LISTENING"))
                .distinct().reduce((a, b) -> a + " " + b).orElse("");
        String suffix = " · " + latency + (badges.isEmpty() ? "" : " · " + badges);
        float scale = .55f;
        int available = (int)((width - 47) / scale);
        String prefix = complete + "/13 ";
        String summary = prefix + ellipsize(mc, label("current_rung", "log"),
                available - mc.textRenderer.getWidth(prefix + suffix));
        context.getMatrices().push();
        context.getMatrices().scale(scale, scale, 1);
        int x = (int)(51 / scale), y = (int)(9 / scale);
        context.drawText(mc.textRenderer, summary, x, y, 0xEDF4FF, false);
        x += mc.textRenderer.getWidth(summary);
        context.drawText(mc.textRenderer, " · ", x, y, 0xEDF4FF, false);
        x += mc.textRenderer.getWidth(" · ");
        context.drawText(mc.textRenderer, latency, x, y, seconds < 2 ? 0x42D9B0 : seconds <= 5 ? 0xFFCF66 : 0xFF7474, false);
        x += mc.textRenderer.getWidth(latency);
        if (!badges.isEmpty()) context.drawText(mc.textRenderer, " · " + badges, x, y, 0xEDF4FF, false);
        String detail = now < objectiveUntil ? label("objective", "")
                : "↳ " + label("intent", "") + " → " + label("action", "");
        context.drawText(mc.textRenderer, ellipsize(mc, detail.replaceAll("[\\r\\n]+", " "),
                (int)((width - 8) / scale)), (int)(10 / scale), (int)(21 / scale), 0xEDF4FF, false);
        context.getMatrices().pop();
    }

    private static String ellipsize(MinecraftClient mc, String text, int width) {
        if (mc.textRenderer.getWidth(text) <= width) return text;
        return mc.textRenderer.trimToWidth(text, Math.max(0, width - mc.textRenderer.getWidth("…"))) + "…";
    }

    private static String label(String key, String fallback) {
        JsonElement value = overlay.get(key);
        return value == null || value.isJsonNull() ? fallback : value.isJsonPrimitive() ? value.getAsString() : value.toString();
    }
    private static final Gson JSON = new Gson();
    private static final ScheduledExecutorService TIMER = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "observatory-timeouts"); thread.setDaemon(true); return thread;
    });
    private static final Queue<Capture> captures = new ConcurrentLinkedQueue<>();
    private static final Set<WebSocket> subscribers = ConcurrentHashMap.newKeySet();
    private static final Set<Integer> guiButtons = new HashSet<>();
    private final Map<Integer, Long> releases = new HashMap<>();
    private static volatile boolean paused;
    private static final SyntheticMouse syntheticMouse = new SyntheticMouse();
    private static volatile boolean portalTouched;
    private static volatile boolean portalLit;
    private final List<PortalAttempt> portalAttempts = new ArrayList<>();
    private final CastingEvidence casting = new CastingEvidence();
    private ServerWorld castingWorld;
    private MinecraftServer castingServer;
    private Vec3d castingOrigin;

    private Map<Long, Double> nearbyObsidian(ServerPlayerEntity player) {
        Map<Long, Double> found = new HashMap<>();
        if (castingOrigin == null) return found;
        BlockPos origin = BlockPos.ofFloored(castingOrigin);
        for (BlockPos pos : BlockPos.iterateOutwards(origin, 6, 6, 6)) {
            if (pos.toCenterPos().squaredDistanceTo(castingOrigin) <= 36
                    && player.getServerWorld().getBlockState(pos).isOf(Blocks.OBSIDIAN))
                found.put(pos.asLong(), pos.toCenterPos().squaredDistanceTo(player.getPos()));
        }
        return found;
    }
    private static volatile JsonObject serverState = new JsonObject();
    private static long frames;
    private static long started = System.nanoTime();
    private static long lastPush;
    private boolean worldStarted;
    private static volatile Reset reset;
    private int readyTicks;
    private static double cursorX, cursorY;
    private static boolean syntheticShift;

    private record Capture(WebSocket socket, JsonElement id, AtomicBoolean done) {}
    private record PortalAttempt(ServerWorld world, BlockPos position, UUID player) {}
    private static class Reset {
        final WebSocket socket; final JsonElement id; final String world; final List<ItemStack> inventory;
        final JsonObject spawn;
        final long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(120);
        volatile boolean granted;
        Reset(WebSocket socket, JsonElement id, String world, List<ItemStack> inventory, JsonObject spawn) {
            this.socket = socket; this.id = id; this.world = world; this.inventory = inventory;
            this.spawn = spawn;
        }
    }

    public static boolean isPaused() { return paused; }
    public static boolean cursorLocked(boolean physical) {
        MinecraftClient mc = MinecraftClient.getInstance();
        return syntheticMouse.cursorLocked(physical, mc.world != null && mc.currentScreen == null && reset == null);
    }
    public static boolean isShiftHeld() { return syntheticShift; }
    public static void touchPortal(ServerPlayerEntity player) {
        if (reset == null && player.getServer() == MinecraftClient.getInstance().getServer()) portalTouched = true;
    }

    public static void beginFrame() {
        overlayDrawn = false;
    }

    public static void endFrame() {
        frames++;
        boolean cleanFallback = !overlayDrawn && !captures.isEmpty();
        Capture pending;
        if (!overlayDrawn) while ((pending = captures.poll()) != null) capture(pending, "end_frame_fallback");
        if (cleanFallback || subscribers.isEmpty() || System.nanoTime() - lastPush < 200_000_000L) return;
        lastPush = System.nanoTime();
        try {
            JsonObject frame = viewerImage();
            JsonObject state = state();
            for (WebSocket socket : subscribers) {
                if (socket.hasBufferedData()) continue;
                push(socket, "frame", frame); push(socket, "state", state);
            }
        } catch (Exception error) {
            for (WebSocket socket : subscribers) push(socket, "stream_error", new JsonPrimitive(error.toString()));
        }
    }

    private static JsonObject viewerImage() throws Exception {
        try (NativeImage image = ScreenshotRecorder.takeScreenshot(MinecraftClient.getInstance().getFramebuffer())) {
            BufferedImage source = ImageIO.read(new ByteArrayInputStream(image.getBytes()));
            BufferedImage rgb = new BufferedImage(source.getWidth(), source.getHeight(), BufferedImage.TYPE_INT_RGB);
            var graphics = rgb.createGraphics(); graphics.drawImage(source, 0, 0, null); graphics.dispose();
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); ImageIO.write(rgb, "jpeg", bytes);
            JsonObject result = new JsonObject();
            result.addProperty("jpeg_base64", Base64.getEncoder().encodeToString(bytes.toByteArray()));
            result.addProperty("width", source.getWidth()); result.addProperty("height", source.getHeight());
            result.addProperty("capture_point", "end_frame_viewer"); return result;
        }
    }

    private static JsonObject image(String point) throws Exception {
        try (NativeImage image = ScreenshotRecorder.takeScreenshot(MinecraftClient.getInstance().getFramebuffer())) {
            JsonObject result = new JsonObject();
            result.addProperty("png_base64", Base64.getEncoder().encodeToString(image.getBytes()));
            result.addProperty("width", image.getWidth()); result.addProperty("height", image.getHeight());
            result.addProperty("capture_point", point); return result;
        }
    }

    private static void capture(Capture capture, String point) {
        if (capture.done().get()) return;
        try {
            JsonObject result = image(point);
            if (capture.done().compareAndSet(false, true)) reply(capture.socket(), capture.id(), result);
        } catch (Exception error) {
            if (capture.done().compareAndSet(false, true)) error(capture.socket(), capture.id(), error);
        }
    }

    private static void requestCapture(WebSocket socket, JsonElement id) {
        Capture capture = new Capture(socket, id, new AtomicBoolean());
        captures.add(capture);
        TIMER.schedule(() -> {
            if (capture.done().compareAndSet(false, true)) {
                captures.remove(capture); error(socket, id, new TimeoutException("Screenshot exceeded 5 seconds"));
            }
        }, 5, TimeUnit.SECONDS);
    }

    @Override public void onInitializeClient() {
        MinecraftClient mc = MinecraftClient.getInstance();
        UseItemCallback.EVENT.register((player, world, hand) -> {
            if (world instanceof ServerWorld serverWorld && player instanceof ServerPlayerEntity serverPlayer
                    && player.getStackInHand(hand).isOf(Items.WATER_BUCKET)) {
                if (castingServer != serverWorld.getServer()) {
                    casting.reset(); castingServer = serverWorld.getServer();
                }
                if (castingWorld != serverWorld) casting.clearAttempt();
                castingWorld = serverWorld;
                castingOrigin = player.getPos();
                casting.begin(serverWorld.getTime(), serverPlayer.getStatHandler().getStat(Stats.USED.getOrCreateStat(Items.WATER_BUCKET)), nearbyObsidian(serverPlayer).keySet());
            }
            return TypedActionResult.pass(player.getStackInHand(hand));
        });
        UseBlockCallback.EVENT.register((player, world, hand, hit) -> {
            var held = player.getStackInHand(hand);
            BlockPos position = hit.getBlockPos().offset(hit.getSide());
            if (world instanceof ServerWorld serverWorld && player instanceof ServerPlayerEntity
                    && (held.isOf(Items.FLINT_AND_STEEL) || held.isOf(Items.FIRE_CHARGE))
                    && !world.getBlockState(position).isOf(Blocks.NETHER_PORTAL)) {
                portalAttempts.add(new PortalAttempt(serverWorld, position.toImmutable(), player.getUuid()));
            }
            return ActionResult.PASS;
        });
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            if (server != mc.getServer()) return;
            if (server.getPlayerManager().getPlayerList().isEmpty()) return;
            ServerPlayerEntity player = server.getPlayerManager().getPlayerList().get(0);
            if (castingServer != server) {
                casting.reset(); castingServer = server;
            }
            if (castingWorld != player.getServerWorld()) {
                casting.clearAttempt(); castingWorld = player.getServerWorld(); castingOrigin = null;
            }
            if (!casting.active(player.getServerWorld().getTime())) castingOrigin = null;
            boolean obsidianCreated = casting.observe(player.getServerWorld().getTime(),
                    player.getStatHandler().getStat(Stats.USED.getOrCreateStat(Items.WATER_BUCKET)), nearbyObsidian(player));
            for (PortalAttempt attempt : portalAttempts) {
                if (attempt.world() == player.getServerWorld() && attempt.player().equals(player.getUuid()) && attempt.world().getBlockState(attempt.position()).isOf(Blocks.NETHER_PORTAL)) portalLit = true;
            }
            portalAttempts.clear();
            Reset pending = reset;
            if (pending != null && !pending.granted && server.getSaveProperties().getLevelName().equals(pending.world)) {
                player.getInventory().clear();
                for (ItemStack item : pending.inventory) player.getInventory().insertStack(item.copy());
                player.setHealth(player.getMaxHealth()); player.getHungerManager().setFoodLevel(20);
                player.getHungerManager().setSaturationLevel(5); player.clearStatusEffects(); player.extinguish();
                if (pending.spawn != null) {
                    JsonObject spawn = pending.spawn;
                    ServerWorld world = player.getServerWorld();
                    world.getGameRules().get(GameRules.DO_DAYLIGHT_CYCLE).set(false, server);
                    world.getGameRules().get(GameRules.DO_WEATHER_CYCLE).set(false, server);
                    world.setTimeOfDay(spawn.get("time_of_day").getAsLong());
                    world.setWeather(0, 0, false, false);
                    player.teleport(world, spawn.get("x").getAsDouble(), spawn.get("y").getAsDouble(), spawn.get("z").getAsDouble(),
                            spawn.get("yaw").getAsFloat(), spawn.get("pitch").getAsFloat());
                    player.setVelocity(Vec3d.ZERO); player.fallDistance = 0;
                }
                player.currentScreenHandler.sendContentUpdates(); pending.granted = true;
            }
            if (pending != null && pending.granted && pending.spawn != null) {
                JsonObject spawn = pending.spawn;
                player.teleport(player.getServerWorld(), spawn.get("x").getAsDouble(), spawn.get("y").getAsDouble(), spawn.get("z").getAsDouble(),
                        spawn.get("yaw").getAsFloat(), spawn.get("pitch").getAsFloat());
                player.setVelocity(Vec3d.ZERO); player.fallDistance = 0;
            }
            if (server.getTicks() % 10 != 0 && (pending == null || !pending.granted)) return;
            JsonObject snapshot = new JsonObject();
            snapshot.addProperty("world_id", server.getSaveProperties().getLevelName());
            snapshot.addProperty("seed", player.getServerWorld().getSeed());
            snapshot.addProperty("portal_entered", portalTouched);
            snapshot.addProperty("obsidian_created", obsidianCreated);
            snapshot.addProperty("portal_lit", portalLit);
            snapshot.addProperty("server_time", player.getServerWorld().getTime());
            snapshot.addProperty("time_of_day", player.getServerWorld().getTimeOfDay());
            JsonObject stats = new JsonObject();
            JsonObject picked = new JsonObject(), crafted = new JsonObject(), mined = new JsonObject(), killed = new JsonObject();
            var handler = player.getStatHandler();
            Registries.ITEM.forEach(item -> {
                int p = handler.getStat(Stats.PICKED_UP, item), c = handler.getStat(Stats.CRAFTED, item);
                if (p > 0) picked.addProperty(Registries.ITEM.getId(item).toString(), p);
                if (c > 0) crafted.addProperty(Registries.ITEM.getId(item).toString(), c);
            });
            Registries.BLOCK.forEach(block -> {
                int n = handler.getStat(Stats.MINED, block);
                if (n > 0) mined.addProperty(Registries.BLOCK.getId(block).toString(), n);
            });
            Registries.ENTITY_TYPE.forEach(type -> {
                int n = handler.getStat(Stats.KILLED, type);
                if (n > 0) killed.addProperty(Registries.ENTITY_TYPE.getId(type).toString(), n);
            });
            stats.add("picked_up", picked); stats.add("crafted", crafted); stats.add("mined", mined); stats.add("killed", killed);
            snapshot.add("stats", stats);
            JsonArray advancements = new JsonArray();
            for (var entry : server.getAdvancementLoader().getAdvancements()) {
                if (player.getAdvancementTracker().getProgress(entry).isDone()) advancements.add(entry.id().toString());
            }
            snapshot.add("advancements", advancements); serverState = snapshot;
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (!worldStarted && client.currentScreen instanceof TitleScreen && client.getOverlay() == null) {
                worldStarted = true; configure(client);
                String world = "observatory-spike";
                if (java.nio.file.Files.exists(client.runDirectory.toPath().resolve("saves").resolve(world).resolve("level.dat"))) {
                    client.createIntegratedServerLoader().start(world, () -> client.setScreen(new TitleScreen()));
                } else createWorld(world, 12345L);
            }
            if (paused) { KeyBinding.unpressAll(); releases.clear(); }
            long now = System.nanoTime();
            releases.entrySet().removeIf(entry -> {
                if (now < entry.getValue()) return false;
                setKey(entry.getKey(), false); return true;
            });
            Reset pending = reset;
            if (pending != null) {
                if (now > pending.deadline) {
                    reset = null; error(pending.socket, pending.id, new TimeoutException("World reset exceeded 120 seconds"));
                } else if (pending.granted && client.player != null && client.world != null && client.currentScreen == null) {
                    if (!client.worldRenderer.isTerrainRenderComplete()) readyTicks = 0;
                    else if (++readyTicks == 80) {
                        client.getServer().submit(() -> client.getServer().getTickManager().setFrozen(pending.spawn != null)).whenComplete((unused, failure) -> client.execute(() -> {
                            if (failure != null) { reset = null; error(pending.socket, pending.id, failure); }
                            else { paused = pending.spawn != null; reset = null; reply(pending.socket, pending.id, state()); }
                        }));
                    }
                }
            }
        });
        WebSocketServer server = new WebSocketServer(new InetSocketAddress("127.0.0.1", 8765)) {
            @Override public org.java_websocket.handshake.ServerHandshakeBuilder onWebsocketHandshakeReceivedAsServer(
                    WebSocket socket, org.java_websocket.drafts.Draft draft, ClientHandshake handshake) throws org.java_websocket.exceptions.InvalidDataException {
                String allowedOrigins = System.getProperty("observatory.allowedOrigins", System.getenv().getOrDefault("OBS_ALLOWED_ORIGINS", ""));
                if (!HandshakePolicy.accepts(handshake.hasFieldValue("Origin"), handshake.getFieldValue("Origin"),
                        handshake.getFieldValue("Authorization"), allowedOrigins, System.getenv("OBS_TOKEN"))) {
                    throw new org.java_websocket.exceptions.InvalidDataException(1008, "Unauthorized WebSocket handshake");
                }
                return super.onWebsocketHandshakeReceivedAsServer(socket, draft, handshake);
            }
            @Override public void onOpen(WebSocket socket, ClientHandshake handshake) {}
            @Override public void onClose(WebSocket socket, int code, String reason, boolean remote) {
                subscribers.remove(socket); mc.execute(() -> { releases.clear(); releaseInputs(); });
            }
            @Override public void onError(WebSocket socket, Exception error) { System.err.println("Observatory: " + error); }
            @Override public void onStart() { System.out.println("OBSERVATORY_READY ws://127.0.0.1:8765"); }
            @Override public void onMessage(WebSocket socket, String message) {
                try {
                    JsonObject request = JsonParser.parseString(message).getAsJsonObject();
                    if (request.has("command") && request.get("command").getAsString().equals("screenshot")) requestCapture(socket, request.get("id"));
                    else mc.execute(() -> handle(socket, request));
                } catch (Exception failure) { error(socket, null, failure); }
            }
        };
        server.setReuseAddr(true); server.setConnectionLostTimeout(30); server.start();
    }

    private static void configure(MinecraftClient mc) {
        mc.options.getViewDistance().setValue(renderDistance); mc.options.getSimulationDistance().setValue(5);
        mc.options.getMaxFps().setValue(60); mc.options.getEnableVsync().setValue(false);
        mc.options.pauseOnLostFocus = false; mc.options.getGuiScale().setValue(2); mc.options.hudHidden = false;
        mc.options.getFov().setValue(70); mc.options.getMouseSensitivity().setValue(.5);
        mc.options.getAutoJump().setValue(autoJump); mc.options.getBobView().setValue(false);
        mc.options.getFovEffectScale().setValue(0.0); mc.options.getDistortionEffectScale().setValue(0.0);
        mc.options.getDarknessEffectScale().setValue(0.0); mc.options.getDamageTiltStrength().setValue(0.0);
        mc.options.getGamma().setValue(1.0); mc.options.getEntityShadows().setValue(false);
        mc.options.getCloudRenderMode().setValue(net.minecraft.client.option.CloudRenderMode.OFF);
        mc.options.getParticles().setValue(net.minecraft.client.option.ParticlesMode.DECREASED);
        mc.options.getShowSubtitles().setValue(true); mc.options.advancedItemTooltips = true;
        if (mc.getWindow().getWidth() != windowWidth || mc.getWindow().getHeight() != 480)
            GLFW.glfwSetWindowSize(mc.getWindow().getHandle(), windowWidth, 480);
        mc.options.setPerspective(net.minecraft.client.option.Perspective.FIRST_PERSON);
        if (mc.getDebugHud().shouldShowDebugHud()) mc.getDebugHud().toggleDebugHud();
        mc.options.tutorialStep = net.minecraft.client.tutorial.TutorialStep.NONE; mc.options.write();
    }

    private static void createWorld(String world, long seed) {
        MinecraftClient mc = MinecraftClient.getInstance();
        mc.createIntegratedServerLoader().createAndStart(world,
                new LevelInfo(world, GameMode.SURVIVAL, false, Difficulty.NORMAL, false, new GameRules(), DataConfiguration.SAFE_MODE),
                new GeneratorOptions(seed, true, false),
                registries -> registries.get(RegistryKeys.WORLD_PRESET).getOrThrow(WorldPresets.DEFAULT).createDimensionsRegistryHolder(),
                new TitleScreen());
    }

    private static JsonObject state() {
        MinecraftClient mc = MinecraftClient.getInstance();
        JsonObject result = serverState.deepCopy();
        result.addProperty("in_world", mc.player != null && reset == null);
        result.addProperty("resetting", reset != null); result.addProperty("paused", paused);
        result.addProperty("pause_on_lost_focus", mc.options.pauseOnLostFocus);
        result.addProperty("window_focused", mc.isWindowFocused());
        result.addProperty("cursor_locked", mc.mouse.isCursorLocked());
        result.addProperty("fps", mc.getCurrentFps()); result.addProperty("frames", frames);
        result.addProperty("elapsed_seconds", (System.nanoTime() - started) / 1e9);
        result.addProperty("render_distance", mc.options.getViewDistance().getValue());
        result.addProperty("renderer", com.mojang.blaze3d.platform.GlDebugInfo.getRenderer());
        result.addProperty("screen_open", mc.currentScreen != null);
        result.addProperty("screen", mc.currentScreen == null ? "" : mc.currentScreen.getClass().getSimpleName());
        if (mc.player != null) {
            result.addProperty("dimension", mc.world.getRegistryKey().getValue().toString());
            result.addProperty("raining", mc.world.isRaining()); result.addProperty("thundering", mc.world.isThundering());
            result.addProperty("food", mc.player.getHungerManager().getFoodLevel());
            result.addProperty("x", mc.player.getX()); result.addProperty("y", mc.player.getY()); result.addProperty("z", mc.player.getZ());
            result.addProperty("yaw", mc.player.getYaw()); result.addProperty("pitch", mc.player.getPitch());
            result.addProperty("health", mc.player.getHealth()); result.addProperty("creative", mc.player.isCreative());
            JsonArray inventory = new JsonArray();
            for (int i = 0; i < mc.player.getInventory().size(); i++) {
                var stack = mc.player.getInventory().getStack(i);
                if (!stack.isEmpty()) {
                    JsonObject item = new JsonObject(); item.addProperty("slot", i);
                    item.addProperty("id", Registries.ITEM.getId(stack.getItem()).toString()); item.addProperty("count", stack.getCount());
                    inventory.add(item);
                }
            }
            result.add("inventory", inventory);
        }
        return result;
    }

    private static void setKey(int code, boolean pressed) {
        if (code == GLFW.GLFW_KEY_LEFT_SHIFT || code == GLFW.GLFW_KEY_RIGHT_SHIFT) syntheticShift = pressed;
        MinecraftClient mc = MinecraftClient.getInstance();
        mc.keyboard.onKey(mc.getWindow().getHandle(), code, GLFW.glfwGetKeyScancode(code),
                pressed ? GLFW.GLFW_PRESS : GLFW.GLFW_RELEASE, 0);
    }

    private static void releaseInputs() {
        syntheticMouse.release();
        syntheticShift = false;
        MinecraftClient mc = MinecraftClient.getInstance(); KeyBinding.unpressAll();
        if (mc.currentScreen != null) for (int button : guiButtons) mc.currentScreen.mouseReleased(cursorX, cursorY, button);
        guiButtons.clear();
    }

    private void handle(WebSocket socket, JsonObject request) {
        MinecraftClient mc = MinecraftClient.getInstance(); JsonElement id = request.get("id");
        try {
            String command = request.get("command").getAsString(); JsonObject result = new JsonObject();
            switch (command) {
                case "configure" -> {
                    if (request.has("auto_jump")) {
                        if (!request.get("auto_jump").isJsonPrimitive() || !request.getAsJsonPrimitive("auto_jump").isBoolean()) throw new IllegalArgumentException("auto_jump must be boolean");
                        autoJump = request.get("auto_jump").getAsBoolean();
                    }
                    if (request.has("render_distance")) {
                        int distance = request.get("render_distance").getAsInt();
                        if (distance < 2 || distance > 12) throw new IllegalArgumentException("render_distance must be 2..12");
                        renderDistance = distance;
                    }
                    if (request.has("width")) {
                        int width = request.get("width").getAsInt();
                        if (width != 640 && width != 854) throw new IllegalArgumentException("width must be 640 or 854");
                        windowWidth = width;
                    }
                    configure(mc);
                }
                case "overlay" -> {
                    JsonObject payload = request.getAsJsonObject("payload");
                    if (payload == null || payload.toString().length() > 8192) throw new IllegalArgumentException("Overlay payload must be an object under 8192 characters");
                    if (payload.has("completed")) payload.addProperty("completed", Math.max(0, Math.min(13, payload.get("completed").getAsInt())));
                    long now = System.nanoTime();
                    if (!Objects.equals(payload.get("objective"), overlay.get("objective"))
                            || (payload.has("badges") && payload.get("badges").getAsString().contains("STARTING")
                            && !label("badges", "").contains("STARTING"))) objectiveUntil = now + 3_000_000_000L;
                    if (payload.has("completed") && payload.get("completed").getAsInt()
                            > (overlay.has("completed") ? overlay.get("completed").getAsInt() : 0)) completionUntil = now + 2_000_000_000L;
                    overlay = payload.deepCopy();
                }
                case "screenshot" -> {
                    requestCapture(socket, id); return;
                }
                case "state" -> result = state();
                case "pause", "resume" -> {
                    if (reset != null || mc.getServer() == null || mc.player == null) throw new IllegalStateException("No ready integrated world");
                    boolean freeze = command.equals("pause");
                    paused = freeze; releases.clear(); releaseInputs();
                    mc.getServer().submit(() -> mc.getServer().getTickManager().setFrozen(freeze)).whenComplete((unused, failure) -> {
                        if (failure != null) error(socket, id, failure);
                        else { JsonObject value = new JsonObject(); value.addProperty("paused", freeze); reply(socket, id, value); }
                    }); return;
                }
                case "reset" -> {
                    if (reset != null) throw new IllegalStateException("Reset already in progress");
                    long seed = request.get("seed").getAsLong(); List<ItemStack> items = new ArrayList<>();
                    Map<Item, Integer> quantities = new LinkedHashMap<>();
                    if (request.has("inventory")) {
                        for (JsonElement value : request.getAsJsonArray("inventory")) {
                            JsonObject item = value.getAsJsonObject(); Identifier name = new Identifier(item.get("id").getAsString());
                            if (!Registries.ITEM.containsId(name) || name.toString().equals("minecraft:air")) throw new IllegalArgumentException("Unknown item " + name);
                            int count = item.get("count").getAsInt(); var type = Registries.ITEM.get(name);
                            if (count < 1) throw new IllegalArgumentException("Invalid item count " + count);
                            quantities.merge(type, count, Math::addExact);
                        }
                    }
                    for (var entry : quantities.entrySet()) {
                        for (int size : StartingStacks.split(entry.getValue(), entry.getKey().getMaxCount(), 36 - items.size())) {
                            items.add(new ItemStack(entry.getKey(), size));
                        }
                    }
                    JsonObject spawn = request.has("spawn") ? request.getAsJsonObject("spawn") : null;
                    if (spawn != null) {
                        for (String key : List.of("x", "y", "z", "yaw", "pitch", "time_of_day")) {
                            if (!spawn.has(key) || !Double.isFinite(spawn.get(key).getAsDouble())) throw new IllegalArgumentException("Invalid spawn " + key);
                        }
                        if (Math.abs(spawn.get("x").getAsDouble()) > 29999984 || Math.abs(spawn.get("z").getAsDouble()) > 29999984
                                || spawn.get("y").getAsDouble() < -64 || spawn.get("y").getAsDouble() > 320
                                || Math.abs(spawn.get("yaw").getAsDouble()) > 180 || Math.abs(spawn.get("pitch").getAsDouble()) > 90
                                || spawn.get("time_of_day").getAsDouble() < 0 || spawn.get("time_of_day").getAsDouble() > 23999) throw new IllegalArgumentException("Spawn out of bounds");
                    }
                    worldStarted = true; configure(mc);
                    paused = false; releases.clear(); releaseInputs(); portalTouched = false; portalLit = false; serverState = new JsonObject();
                    String world = "observatory-" + UUID.randomUUID(); readyTicks = 0;
                    reset = new Reset(socket, id, world, items, spawn);
                    mc.disconnect(new TitleScreen()); createWorld(world, seed); return;
                }
                case "key" -> {
                    if (paused || reset != null) { result.addProperty("ignored", true); break; }
                    int code = request.get("code").getAsInt(); boolean pressed = !request.has("pressed") || request.get("pressed").getAsBoolean();
                    long ms = request.has("duration_ms") ? request.get("duration_ms").getAsLong() : -1;
                    if (ms < -1 || ms > 60000) throw new IllegalArgumentException("duration_ms must be 0..60000");
                    if (code == GLFW.GLFW_KEY_LEFT_SHIFT || code == GLFW.GLFW_KEY_RIGHT_SHIFT) syntheticShift = pressed;
                    if (mc.currentScreen != null) {
                        if (pressed) mc.currentScreen.keyPressed(code, GLFW.glfwGetKeyScancode(code), 0);
                        else mc.currentScreen.keyReleased(code, GLFW.glfwGetKeyScancode(code), 0);
                    } else { setKey(code, pressed); }
                    releases.remove(code);
                    if (pressed && ms >= 0) releases.put(code, System.nanoTime() + ms * 1_000_000L);
                }
                case "mouse" -> {
                    if (paused || reset != null) { result.addProperty("ignored", true); break; }
                    if (mc.player == null) throw new IllegalStateException("Not in world");
                    if ((request.has("x") || request.has("y")) && mc.currentScreen == null) {
                        throw new IllegalArgumentException("Coordinate clicks require an open GUI; in-world clicks target the crosshair. Use look to aim, or omit coordinates.");
                    }
                    if (mc.currentScreen != null) {
                        if (request.has("x") && request.has("y")) {
                            double x = request.get("x").getAsDouble(), y = request.get("y").getAsDouble();
                            if (x < 0 || y < 0 || x >= mc.getWindow().getFramebufferWidth() || y >= mc.getWindow().getFramebufferHeight()) throw new IllegalArgumentException("Cursor outside framebuffer");
                            cursorX = x * mc.getWindow().getScaledWidth() / mc.getWindow().getFramebufferWidth();
                            cursorY = y * mc.getWindow().getScaledHeight() / mc.getWindow().getFramebufferHeight();
                            GLFW.glfwSetCursorPos(mc.getWindow().getHandle(), x * mc.getWindow().getWidth() / mc.getWindow().getFramebufferWidth(), y * mc.getWindow().getHeight() / mc.getWindow().getFramebufferHeight());
                            mc.currentScreen.mouseMoved(cursorX, cursorY);
                        }
                        if (request.has("button")) {
                            int button = request.get("button").getAsInt(); if (button < 0 || button > 1) throw new IllegalArgumentException("button must be 0 or 1");
                            syntheticMouse.button(button, false);
                            if (request.get("pressed").getAsBoolean()) { mc.currentScreen.mouseClicked(cursorX, cursorY, button); guiButtons.add(button); }
                            else { mc.currentScreen.mouseReleased(cursorX, cursorY, button); guiButtons.remove(button); }
                        }
                    } else {
                        double dx = request.has("dx") ? request.get("dx").getAsDouble() : 0, dy = request.has("dy") ? request.get("dy").getAsDouble() : 0;
                        mc.player.changeLookDirection(dx, dy);
                        if (request.has("button")) {
                            int button = request.get("button").getAsInt(); if (button < 0 || button > 1) throw new IllegalArgumentException("button must be 0 or 1");
                            InputUtil.Key key = InputUtil.Type.MOUSE.createFromCode(button); boolean pressed = request.get("pressed").getAsBoolean();
                            syntheticMouse.button(button, pressed);
                            KeyBinding.setKeyPressed(key, pressed); if (pressed) KeyBinding.onKeyPressed(key);
                        }
                    }
                }
                case "type" -> {
                    if (paused || reset != null) { result.addProperty("ignored", true); break; }
                    if (mc.currentScreen == null) throw new IllegalStateException("Typing requires an open screen");
                    for (char c : request.get("text").getAsString().toCharArray()) mc.currentScreen.charTyped(c, 0);
                }
                case "subscribe" -> subscribers.add(socket);
                case "unsubscribe" -> subscribers.remove(socket);
                case "fps_reset" -> { frames = 0; started = System.nanoTime(); }
                default -> throw new IllegalArgumentException("Unknown command: " + command);
            }
            reply(socket, id, result);
        } catch (Exception failure) { error(socket, id, failure); }
    }

    private static void push(WebSocket socket, String event, JsonElement data) {
        JsonObject value = new JsonObject(); value.addProperty("event", event); value.add("data", data); send(socket, value);
    }
    private static void reply(WebSocket socket, JsonElement id, JsonObject result) {
        JsonObject value = new JsonObject(); if (id != null) value.add("id", id);
        value.addProperty("ok", true); value.add("result", result); send(socket, value);
    }
    private static void error(WebSocket socket, JsonElement id, Throwable failure) {
        JsonObject value = new JsonObject(); if (id != null) value.add("id", id);
        value.addProperty("ok", false); value.addProperty("error", failure.toString()); send(socket, value);
    }
    private static void send(WebSocket socket, JsonObject value) {
        if (socket.isOpen()) { try { socket.send(JSON.toJson(value)); } catch (Exception ignored) {} }
    }
}
