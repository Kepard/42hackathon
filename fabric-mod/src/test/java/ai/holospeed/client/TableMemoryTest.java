package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class TableMemoryTest {
    @Test void lastVerifiedTableSurvivesSkillBoundariesWithinTheSameWorld() {
        Object world = new Object();
        TableMemory memory = new TableMemory();
        assertNull(memory.position(world));
        var placed = new TableMemory.Position(32, 67, 41);
        memory.remember(world, placed);
        assertEquals(placed, memory.position(world));
        assertEquals(placed, memory.position(world));
        var moreRecentlyUsed = new TableMemory.Position(35, 64, 40);
        memory.remember(world, moreRecentlyUsed);
        assertEquals(moreRecentlyUsed, memory.position(world));
    }

    @Test void equalButDifferentWorldIdentitiesCannotReuseOldCoordinates() {
        Object original = new String("same world name");
        Object replacement = new String("same world name");
        TableMemory memory = new TableMemory();
        memory.remember(original, new TableMemory.Position(0, 64, 0));
        assertNull(memory.position(replacement));
        assertNull(memory.position(original), "changing back does not resurrect forgotten state");
    }

    @Test void WorldUnloadAndDestroyedTablesForgetTheTarget() {
        Object world = new Object();
        TableMemory memory = new TableMemory();
        memory.remember(world, new TableMemory.Position(0, 64, 0));
        assertNull(memory.position(null));
        assertNull(memory.position(world));
        memory.remember(world, new TableMemory.Position(1, 64, 1));
        memory.forget(world);
        assertNull(memory.position(world));
    }

    @Test void InvalidWorldOrCoordinatesCannotBecomeATarget() {
        TableMemory memory = new TableMemory();
        var position = new TableMemory.Position(0, 64, 0);
        assertThrows(IllegalArgumentException.class, () -> memory.remember(null, position));
        assertThrows(IllegalArgumentException.class, () -> memory.remember(new Object(), null));
        assertThrows(IllegalArgumentException.class, () -> new TableMemory.Position(30_000_000, 64, 0));
        assertThrows(IllegalArgumentException.class, () -> new TableMemory.Position(0, -30_000_001, 0));
        assertThrows(IllegalArgumentException.class, () -> new TableMemory.Position(0, 64, 30_000_000));
    }
}
