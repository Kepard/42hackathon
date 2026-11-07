package dev.observatory;

import java.util.ArrayList;
import java.util.List;

final class StartingStacks {
    static List<Integer> split(int count, int maxCount, int slots) {
        if (count < 1 || maxCount < 1 || slots < 0) throw new IllegalArgumentException("Invalid starting stack parameters");
        long required = ((long) count + maxCount - 1) / maxCount;
        if (required > slots) throw new IllegalArgumentException("Starting inventory exceeds 36 slots");
        List<Integer> stacks = new ArrayList<>();
        while (count > 0) {
            int size = Math.min(count, maxCount);
            stacks.add(size);
            count -= size;
        }
        return stacks;
    }
}
