package ai.holospeed.client;

import java.lang.reflect.Method;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/** Read-only public Baritone input telemetry; never forces or releases a key. */
final class SkillInputTelemetry {
    private static final Map<String, String> INPUTS = Map.ofEntries(
        Map.entry("forward", "MOVE_FORWARD"), Map.entry("back", "MOVE_BACK"),
        Map.entry("left", "MOVE_LEFT"), Map.entry("right", "MOVE_RIGHT"),
        Map.entry("jump", "JUMP"), Map.entry("sneak", "SNEAK"),
        Map.entry("sprint", "SPRINT"), Map.entry("attack", "CLICK_LEFT"),
        Map.entry("use", "CLICK_RIGHT"));
    private Object handler;
    private Method forcedDown;
    private Map<String, Object> values;
    private long retryAt;

    /** Null means unavailable, whereas an empty set means no forced keys. */
    Set<String> read() {
        if (handler == null && System.nanoTime() < retryAt) return null;
        try {
            if (handler == null) resolve();
            Set<String> result = new LinkedHashSet<>();
            for (var entry : values.entrySet()) {
                if (Boolean.TRUE.equals(forcedDown.invoke(handler, entry.getValue()))) result.add(entry.getKey());
            }
            return Set.copyOf(result);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError error) {
            handler = null;
            retryAt = System.nanoTime() + 2_000_000_000L;
            return null;
        }
    }

    private void resolve() throws ReflectiveOperationException {
        Class<?> api = Class.forName("baritone.api.BaritoneAPI");
        Method providerGetter = api.getMethod("getProvider");
        Object provider = providerGetter.invoke(null);
        Method primaryGetter = providerGetter.getReturnType().getMethod("getPrimaryBaritone");
        Object primary = primaryGetter.invoke(provider);
        Method inputGetter = primaryGetter.getReturnType().getMethod("getInputOverrideHandler");
        Object input = inputGetter.invoke(primary);
        Class<?> inputEnum = Class.forName("baritone.api.utils.input.Input");
        Method method = inputGetter.getReturnType().getMethod("isInputForcedDown", inputEnum);
        Map<String, Object> mapping = new java.util.LinkedHashMap<>();
        for (var entry : INPUTS.entrySet()) mapping.put(entry.getKey(), inputEnum.getField(entry.getValue()).get(null));
        values = Map.copyOf(mapping);
        forcedDown = method;
        handler = input;
    }
}
