package dev.observatory;

import java.util.*;

public final class InputDisplay {
    public record Line(String text, int alpha) {}
    private record Held(String label, long since) {}
    private record Recent(String label, long at) {}
    private final Map<String, Held> held = new LinkedHashMap<>();
    private final Deque<Recent> recent = new ArrayDeque<>();
    private static final long FADE = 6_000_000_000L;

    public void key(int code, boolean pressed, long now) { set("K" + code, chord(keyLabel(code), code), pressed, now); }
    public void mouse(int button, boolean pressed, long now) { set("M" + button, chord(switch (button) { case 0 -> "LMB"; case 1 -> "RMB"; default -> "MMB"; }, -1), pressed, now); }
    private String chord(String label, int own) {
        String prefix = "";
        for (int code : new int[]{340,344,341,345,342,346,343,347})
            if (code != own && held.containsKey("K" + code)) prefix += keyLabel(code) + "+";
        return prefix + label;
    }
    private void set(String id, String label, boolean pressed, long now) {
        if (pressed) {
            if (!held.containsKey(id)) { held.put(id, new Held(label, now)); note(label, now); }
        } else {
            Held old = held.remove(id);
            if (old != null) note(String.format(Locale.ROOT, "%s hold %.1fs", old.label(), (now-old.since())/1e9), now);
        }
    }
    public void note(String label, long now) {
        recent.addLast(new Recent(label, now));
        while (recent.size() > 8) recent.removeFirst();
    }
    public void clear(long now) {
        for (String id : List.copyOf(held.keySet())) set(id, "", false, now);
    }
    public boolean active(long now) { return !held.isEmpty() || recent.stream().anyMatch(item -> now-item.at() < FADE); }
    public List<Line> lines(long now) {
        recent.removeIf(item -> now-item.at() >= FADE);
        List<Line> lines = new ArrayList<>();
        String heldText = held.values().stream().map(item -> String.format(Locale.ROOT, "%s %.1fs", item.label(), (now-item.since())/1e9)).reduce((a,b) -> a+" · "+b).orElse("—");
        lines.add(new Line("Held: " + heldText, 255));
        List<Recent> tail = new ArrayList<>(recent);
        for (Recent item : tail.subList(Math.max(0, tail.size()-3), tail.size()))
            lines.add(new Line(item.label(), (int)(235 * (1.0-(now-item.at())/(double)FADE)) + 20));
        return lines;
    }
    private static String keyLabel(int code) {
        if (code >= 65 && code <= 90 || code >= 48 && code <= 57) return Character.toString((char)code);
        if (code >= 290 && code <= 314) return "F" + (code-289);
        return switch (code) {
            case 32 -> "Space"; case 256 -> "Esc"; case 257 -> "Enter"; case 258 -> "Tab";
            case 259 -> "Backspace"; case 260 -> "Insert"; case 261 -> "Delete";
            case 262 -> "Right"; case 263 -> "Left"; case 264 -> "Down"; case 265 -> "Up";
            case 340 -> "Shift"; case 344 -> "RShift"; case 341 -> "Ctrl"; case 345 -> "RCtrl";
            case 342 -> "Alt"; case 346 -> "RAlt"; case 343 -> "Super"; case 347 -> "RSuper";
            default -> code >= 33 && code <= 96 ? Character.toString((char)code) : "Key" + code;
        };
    }
}
