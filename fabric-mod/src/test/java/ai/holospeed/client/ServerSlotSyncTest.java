package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ServerSlotSyncTest {
    @Test void waterPickupRequiresFreshServerOwnedHotbarItemEvenWhenSourceRegenerates() {
        ServerSlotSync sync = new ServerSlotSync(); Object connection = new Object();
        sync.record(connection, 0, 38, "minecraft:bucket", 1);
        long before = sync.latest(connection);
        assertFalse(sync.matches(connection, 2, before, "minecraft:water_bucket", 1));
        sync.record(connection, 4, 38, "minecraft:water_bucket", 1);
        assertFalse(sync.matches(connection, 2, before, "minecraft:water_bucket", 1), "unrelated container slot is not hotbar proof");
        sync.record(connection, 0, 38, "minecraft:water_bucket", 1);
        assertTrue(sync.matches(connection, 2, before, "minecraft:water_bucket", 1));
        assertFalse(sync.matches(connection, 1, before, "minecraft:water_bucket", 1));
        assertFalse(sync.matches(connection, 2, sync.latest(connection), "minecraft:water_bucket", 1));
    }

    @Test void directInventoryPacketsMapToSameHotbarAndConnectionResetInvalidatesProof() {
        ServerSlotSync sync = new ServerSlotSync(); Object first = new Object(), second = new Object();
        sync.record(first, -2, 3, "minecraft:lava_bucket", 1);
        assertTrue(sync.matches(first, 3, 0, "minecraft:lava_bucket", 1));
        sync.record(second, 0, 36, "minecraft:bucket", 1);
        assertFalse(sync.matches(second, 3, 0, "minecraft:lava_bucket", 1));
        assertFalse(sync.matches(first, 3, 0, "minecraft:lava_bucket", 1));
        sync.record(second, -2, 0, "minecraft:water_bucket", 0);
        assertFalse(sync.matches(second, 0, 0, "minecraft:water_bucket", 1));
    }
}
