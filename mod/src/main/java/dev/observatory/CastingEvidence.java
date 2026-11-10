package dev.observatory;

import java.util.Map;
import java.util.Set;

final class CastingEvidence {
    private Set<Long> before = Set.of();
    private long expires;
    private int waterUses;
    private boolean active;
    private boolean created;

    void begin(long tick, int uses, Set<Long> existing) {
        before = Set.copyOf(existing);
        expires = tick + 200;
        waterUses = uses;
        active = true;
    }

    boolean observe(long tick, int uses, Map<Long, Double> obsidianDistancesSquared) {
        if (tick > expires) active = false;
        if (active && uses > waterUses && obsidianDistancesSquared.entrySet().stream()
                .anyMatch(e -> e.getValue() <= 36 && !before.contains(e.getKey()))) created = true;
        return created;
    }

    boolean active(long tick) { return active && tick <= expires; }

    void clearAttempt() {
        before = Set.of();
        active = false;
    }

    void reset() {
        clearAttempt();
        created = false;
    }
}
