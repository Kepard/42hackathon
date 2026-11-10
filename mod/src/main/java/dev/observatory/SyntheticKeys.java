package dev.observatory;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

public final class SyntheticKeys {
    private final Set<Integer> held = ConcurrentHashMap.newKeySet();
    public void set(int code, boolean pressed) { if (pressed) held.add(code); else held.remove(code); }
    public boolean held(int code) { return held.contains(code); }
    public void clear() { held.clear(); }
    public int modifiers() {
        return ((held(340) || held(344)) ? 1 : 0) | ((held(341) || held(345)) ? 2 : 0)
            | ((held(342) || held(346)) ? 4 : 0) | ((held(343) || held(347)) ? 8 : 0);
    }
}
