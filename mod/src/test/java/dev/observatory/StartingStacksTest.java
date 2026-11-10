package dev.observatory;

import java.util.List;

public class StartingStacksTest {
    public static void main(String[] args) {
        check(StartingStacks.split(64, 16, 36).equals(List.of(16, 16, 16, 16)));
        check(StartingStacks.split(20, 16, 36).equals(List.of(16, 4)));
        check(StartingStacks.split(64, 64, 36).equals(List.of(64)));
        check(StartingStacks.split(3, 1, 36).equals(List.of(1, 1, 1)));
        check(StartingStacks.split(2304, 64, 36).size() == 36);
        rejects(37, 1, 36);
        rejects(17, 16, 1);
        rejects(1, 64, 0);
        rejects(0, 64, 36);
        rejects(-1, 16, 36);
        rejects(1, 0, 36);
        rejects(Integer.MAX_VALUE, 64, 36);
        System.out.println("PASS starting stacks: 64 pearls -> 4x16; partial, 64-stack and single-stack items; capacity and invalid counts");
    }

    private static void rejects(int count, int maxCount, int slots) {
        try {
            StartingStacks.split(count, maxCount, slots);
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("Expected invalid inventory to be rejected");
    }

    private static void check(boolean value) {
        if (!value) throw new AssertionError();
    }
}
