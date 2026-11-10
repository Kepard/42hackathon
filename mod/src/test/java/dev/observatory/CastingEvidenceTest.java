package dev.observatory;

import java.util.Map;
import java.util.Set;

public class CastingEvidenceTest {
    public static void main(String[] args) {
        CastingEvidence evidence = new CastingEvidence();
        check(!evidence.observe(0, 1, Map.of(1L, 1.0)));
        evidence.begin(10, 1, Set.of(1L));
        check(!evidence.observe(11, 2, Map.of()));
        check(!evidence.observe(11, 2, Map.of(1L, 1.0)));
        check(!evidence.observe(11, 1, Map.of(2L, 1.0)));
        check(!evidence.observe(11, 2, Map.of(2L, 36.01)));
        check(evidence.observe(210, 2, Map.of(2L, 36.0)));
        check(evidence.observe(300, 2, Map.of()));
        evidence.clearAttempt();
        check(!evidence.active(0));
        check(evidence.observe(0, 0, Map.of()));
        evidence.reset();
        check(!evidence.observe(0, 9, Map.of(2L, 1.0)));
        evidence.begin(10, 1, Set.of());
        check(!evidence.observe(211, 2, Map.of(2L, 1.0)));
        evidence.begin(300, 2, Set.of());
        evidence.clearAttempt();
        check(!evidence.observe(301, 3, Map.of(3L, 1.0)));
        System.out.println("PASS cast obsidian: valid new nearby only; travel preserves earned evidence and cancels pending water; new world clears latch");
    }

    private static void check(boolean value) {
        if (!value) throw new AssertionError("Invalid cast evidence");
    }
}
