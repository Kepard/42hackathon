package ai.holospeed.client;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.font.TextRenderer;
import net.minecraft.client.gui.DrawContext;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Presentation only: no game state is read or supplied to the agent. */
public final class HoloHud {
    private static final int BACKGROUND = 0xDC0A101C;
    private static final int BORDER = 0x8050637D;
    private static final int TEXT = 0xFFE7EFFB;
    private static final int MUTED = 0xFFA3B2CA;
    private static final int CYAN = 0xFF55E7DD;
    private static final int PURPLE = 0xFFB799FF;
    private static final int AMBER = 0xFFFFCD77;

    private HoloHud() {}

    public static void render(DrawContext context, MinecraftClient client,
                              JsonObject status, Set<String> heldKeys,
                              boolean connected, boolean enabled) {
        if (client == null || client.textRenderer == null || client.getWindow() == null) {
            return;
        }
        float scale = (float) (Math.max(1.0, Math.min(1.5, client.getWindow().getFramebufferWidth() / 1000.0)) / client.getWindow().getScaleFactor());
        int available = (int) (client.getWindow().getScaledWidth() / scale) - 20;
        if (available < 170 || client.getWindow().getScaledHeight() / scale < 160) {
            return;
        }
        context.getMatrices().push();
        context.getMatrices().scale(scale, scale, 1);
        int x = 10;
        int y = 10;
        int width = Math.min(286, available);
        int height = 142;
        TextRenderer font = client.textRenderer;
        JsonObject data = status == null ? new JsonObject() : status;
        Set<String> inputs = heldKeys == null ? Set.of() : heldKeys;

        context.fill(x + 2, y + 2, x + width + 2, y + height + 2, 0x48000000);
        context.fill(x, y, x + width, y + height, BACKGROUND);
        context.fill(x, y, x + width, y + 1, BORDER);
        context.fill(x, y + height - 1, x + width, y + height, BORDER);
        context.fill(x, y, x + 2, y + height, CYAN);
        context.fill(x + width / 2, y, x + width, y + 1, PURPLE);

        text(context, font, "HOLO", x + 10, y + 9, CYAN);
        text(context, font, "SPEED", x + 10 + font.getWidth("HOLO"), y + 9, TEXT);
        String timer = timer(number(data, "elapsed_ms", 0));
        text(context, font, timer, x + width - 10 - font.getWidth(timer), y + 9, TEXT);

        String state = !connected ? "OFFLINE" : !enabled ? "PAUSED" :
                string(data, "status", "connected").replace('_', ' ').toUpperCase(Locale.ROOT);
        int stateColor = connected && enabled ? CYAN : AMBER;
        context.fill(x + 10, y + 26, x + 14, y + 30, stateColor);
        text(context, font, fit(font, state + " / F8 STOP / F9 START", width - 33),
                x + 19, y + 24, MUTED);

        context.fill(x + 10, y + 38, x + width - 10, y + 39, 0x55495C76);
        text(context, font, fit(font, string(data, "objective", "Waiting for objective"), width - 20),
                x + 10, y + 45, TEXT);
        text(context, font, fit(font, string(data, "strategy", "Connect the local controller to begin"), width - 20),
                x + 10, y + 57, MUTED);

        String holo = "HOLO4 " + latency(data, "holo_latency_ms");
        String jev = "JEV " + latency(data, "jev_latency_ms");
        text(context, font, holo, x + 10, y + 73, CYAN);
        int jevX = Math.max(x + width / 2, x + 18 + font.getWidth(holo));
        text(context, font, fit(font, jev, x + width - 10 - jevX), jevX, y + 73, PURPLE);

        String[][] keys = {
                {"forward", "W"}, {"left", "A"}, {"back", "S"}, {"right", "D"},
                {"jump", "SPC"}, {"sprint", "RUN"}, {"attack", "LMB"}, {"use", "RMB"}
        };
        int keyX = x + 10;
        for (String[] key : keys) {
            int keyWidth = Math.max(18, font.getWidth(key[1]) + 8);
            if (keyX + keyWidth > x + width - 10) {
                break;
            }
            boolean pressed = inputs.contains(key[0]);
            context.fill(keyX, y + 89, keyX + keyWidth, y + 103,
                    pressed ? 0xD23B9F9F : 0xA3202B3D);
            context.fill(keyX, y + 102, keyX + keyWidth, y + 103,
                    pressed ? CYAN : 0x80566B85);
            text(context, font, key[1], keyX + (keyWidth - font.getWidth(key[1])) / 2,
                    y + 92, pressed ? TEXT : MUTED);
            keyX += keyWidth + 3;
        }

        String progress = milestones(data);
        text(context, font, fit(font, progress, width - 20), x + 10, y + 111, PURPLE);
        String event = string(data, "event", "Visual decisions / bounded inputs");
        text(context, font, fit(font, event, width - 20), x + 10, y + 126, MUTED);
        context.getMatrices().pop();
    }

    private static void text(DrawContext context, TextRenderer font, String text,
                             int x, int y, int color) {
        context.drawText(font, text, x, y, color, false);
    }

    private static String fit(TextRenderer font, String text, int width) {
        if (width <= 0) {
            return "";
        }
        String clean = text.replace('\n', ' ').replace('\r', ' ');
        if (font.getWidth(clean) <= width) {
            return clean;
        }
        return font.trimToWidth(clean, Math.max(0, width - font.getWidth("..."))) + "...";
    }

    private static String latency(JsonObject data, String key) {
        double value = number(data, key, -1);
        if (value < 0) {
            return "--";
        }
        return value < 1000 ? Math.round(value) + "ms" :
                String.format(Locale.ROOT, "%.1fs", value / 1000);
    }

    private static String timer(double millis) {
        long seconds = Math.max(0, (long) millis / 1000);
        long minutes = seconds / 60;
        return String.format(Locale.ROOT, "%02d:%02d", minutes, seconds % 60);
    }

    private static String milestones(JsonObject data) {
        JsonElement value = data.get("milestones");
        if (value == null || !value.isJsonArray()) {
            return "PROGRESS  Awaiting visual confirmation";
        }
        JsonArray milestones = value.getAsJsonArray();
        List<String> names = new ArrayList<>();
        for (JsonElement element : milestones) {
            if (element.isJsonPrimitive() && element.getAsJsonPrimitive().isString()) {
                names.add(element.getAsString().replace('_', ' '));
            } else if (element.isJsonObject()) {
                JsonObject item = element.getAsJsonObject();
                String name = string(item, "name", string(item, "id", ""));
                if (!name.isBlank()) {
                    names.add(name.replace('_', ' '));
                }
            }
        }
        return names.isEmpty() ? "PROGRESS  Awaiting visual confirmation" :
                "PROGRESS  " + String.join(" > ", names);
    }

    private static String string(JsonObject data, String key, String fallback) {
        JsonElement value = data.get(key);
        if (value == null || !value.isJsonPrimitive()
                || !value.getAsJsonPrimitive().isString()) {
            return fallback;
        }
        return value.getAsString();
    }

    private static double number(JsonObject data, String key, double fallback) {
        JsonElement value = data.get(key);
        if (value == null || !value.isJsonPrimitive()
                || !value.getAsJsonPrimitive().isNumber()) {
            return fallback;
        }
        try {
            double parsed = value.getAsDouble();
            return Double.isFinite(parsed) ? parsed : fallback;
        } catch (NumberFormatException ignored) {
            return fallback;
        }
    }
}
