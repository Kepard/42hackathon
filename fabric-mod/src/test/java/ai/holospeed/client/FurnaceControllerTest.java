package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** Pure resource planning only; no Minecraft client, world, or network starts. */
class FurnaceControllerTest {
    @Test void totalInventoryTargetDoesNotLoadOrBurnAfterItIsSatisfied() {
        var plan = FurnaceController.plan(3, 3, 0, 0, 0, false);
        assertTrue(plan.complete());
        assertEquals(0, plan.inputToLoad());
        assertFalse(plan.needsFuel());
        assertTrue(FurnaceController.plan(3, 9, 0, 0, 0, false).complete());
    }

    @Test void existingInputAndOutputAreReservedBeforeNewOreIsMoved() {
        var plan = FurnaceController.plan(6, 1, 2, 1, 0, false);
        assertEquals(5, plan.missingIngots());
        assertEquals(2, plan.inputToLoad());
        assertTrue(plan.needsFuel());
        assertEquals(0, FurnaceController.plan(3, 1, 10, 0, 0, true).inputToLoad());
    }

    @Test void alreadyCookedOutputNeedsCollectionButNoNewHeatOrOre() {
        var plan = FurnaceController.plan(4, 1, 0, 3, 0, false);
        assertFalse(plan.complete());
        assertEquals(0, plan.inputToLoad());
        assertFalse(plan.needsFuel());
    }

    @Test void ExistingHeatOrFuelAvoidsSpendingAnotherFuelItem() {
        assertFalse(FurnaceController.plan(3, 0, 3, 0, 0, true).needsFuel());
        assertFalse(FurnaceController.plan(3, 0, 3, 0, 1, false).needsFuel());
        assertTrue(FurnaceController.plan(3, 0, 3, 0, 0, false).needsFuel());
    }

    @Test void requestBoundsRejectUnboundedWorkAndInvalidResourceCounts() {
        for (int target : new int[]{0, -1, 65, Integer.MAX_VALUE}) assertThrows(IllegalArgumentException.class, () -> FurnaceController.validateBounds(target, 1000));
        for (int ticks : new int[]{0, 19, 1801, Integer.MAX_VALUE}) assertThrows(IllegalArgumentException.class, () -> FurnaceController.validateBounds(3, ticks));
        assertDoesNotThrow(() -> FurnaceController.validateBounds(1, 20));
        assertDoesNotThrow(() -> FurnaceController.validateBounds(64, 1800));
        assertThrows(IllegalArgumentException.class, () -> FurnaceController.plan(3, -1, 0, 0, 0, false));
        assertThrows(IllegalArgumentException.class, () -> FurnaceController.plan(3, 0, 4097, 0, 0, false));
    }

    @Test void coalAndCharcoalArePreferredOverWoodAndToolsAreNeverFuelCandidates() {
        assertTrue(FurnaceController.fuelRank("minecraft:coal") < FurnaceController.fuelRank("minecraft:charcoal"));
        assertTrue(FurnaceController.fuelRank("minecraft:charcoal") < FurnaceController.fuelRank("minecraft:oak_planks"));
        assertTrue(FurnaceController.fuelRank("minecraft:oak_planks") < FurnaceController.fuelRank("minecraft:birch_log"));
        for (String item : new String[]{"minecraft:wooden_pickaxe", "minecraft:crafting_table", "minecraft:stick", "minecraft:crimson_planks", "minecraft:warped_planks", "other:oak_planks"}) {
            assertEquals(Integer.MAX_VALUE, FurnaceController.fuelRank(item), item);
        }
        assertEquals(Integer.MAX_VALUE, FurnaceController.fuelRank(null));
        assertTrue(FurnaceController.fuelRank("minecraft:stripped_mangrove_log") < Integer.MAX_VALUE);
    }

    @Test void onlyRawIronAndBothIronOreVariantsAreAcceptedAsInput() {
        for (String item : new String[]{"minecraft:raw_iron", "minecraft:iron_ore", "minecraft:deepslate_iron_ore"}) assertTrue(FurnaceController.ironInput(item));
        for (String item : new String[]{"minecraft:raw_gold", "minecraft:iron_ingot", "minecraft:raw_iron_block", "minecraft:stone"}) assertFalse(FurnaceController.ironInput(item));
        assertFalse(FurnaceController.ironInput(null));
    }
}
