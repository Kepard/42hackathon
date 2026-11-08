package ai.holospeed.client;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.HashSet;
import java.util.Set;

record ActionSpec(Set<String> keys, int duration, float yaw, float pitch, Integer hotbar, Cursor cursor, int scroll) {
    static final Set<String> KEYS = Set.of("forward", "back", "left", "right", "jump", "sneak", "sprint", "attack", "use", "inventory", "drop", "swap", "escape");
    record Cursor(double x, double y, int button) {}

    static ActionSpec parse(JsonObject value) {
        rejectUnknown(value, Set.of("keys", "duration_ticks", "yaw_delta", "pitch_delta", "hotbar", "cursor", "scroll"));
        Set<String> keys = new HashSet<>();
        if (value.has("keys")) {
            for (JsonElement key : value.getAsJsonArray("keys")) {
                String name = key.getAsString();
                if (!KEYS.contains(name) || !keys.add(name)) throw new IllegalArgumentException("Invalid or duplicate key");
            }
        }
        if (keys.contains("forward") && keys.contains("back") || keys.contains("left") && keys.contains("right")) throw new IllegalArgumentException("Opposing keys");
        int duration = integer(value, "duration_ticks", 6, 1, 80);
        float yaw = (float) number(value, "yaw_delta", 0, -90, 90);
        float pitch = (float) number(value, "pitch_delta", 0, -60, 60);
        Integer hotbar = nullable(value, "hotbar") ? null : integer(value, "hotbar", 1, 1, 9);
        int scroll = integer(value, "scroll", 0, -9, 9);
        Cursor cursor = null;
        if (!nullable(value, "cursor")) {
            JsonObject c = value.getAsJsonObject("cursor");
            rejectUnknown(c, Set.of("x", "y", "button"));
            if (!c.has("x") || !c.has("y")) throw new IllegalArgumentException("Missing cursor coordinates");
            int button = switch (c.get("button").getAsString()) {
                case "left" -> 0; case "right" -> 1; case "middle" -> 2;
                default -> throw new IllegalArgumentException("Invalid mouse button");
            };
            cursor = new Cursor(number(c, "x", 0, 0, 1), number(c, "y", 0, 0, 1), button);
        }
        if (duration > 20 && !(keys.equals(Set.of("attack")) && yaw == 0 && pitch == 0 && hotbar == null && cursor == null && scroll == 0)) {
            throw new IllegalArgumentException("Long hold must be stationary attack only");
        }
        return new ActionSpec(Set.copyOf(keys), duration, yaw, pitch, hotbar, cursor, scroll);
    }

    static int integer(JsonObject value, String key, int fallback, int min, int max) {
        double number = number(value, key, fallback, min, max);
        if (number != Math.rint(number)) throw new IllegalArgumentException("Expected integer: " + key);
        return (int) number;
    }

    private static double number(JsonObject value, String key, double fallback, double min, double max) {
        if (!value.has(key)) return fallback;
        if (!value.get(key).isJsonPrimitive() || !value.getAsJsonPrimitive(key).isNumber()) throw new IllegalArgumentException("Expected number: " + key);
        double number = value.get(key).getAsDouble();
        if (!Double.isFinite(number) || number < min || number > max) throw new IllegalArgumentException("Out of range: " + key);
        return number;
    }

    private static boolean nullable(JsonObject value, String key) { return !value.has(key) || value.get(key).isJsonNull(); }
    private static void rejectUnknown(JsonObject value, Set<String> allowed) {
        if (!allowed.containsAll(value.keySet())) throw new IllegalArgumentException("Unknown action fields");
    }
}
