package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ReturnMaterialPolicyTest {
    @Test void allSupportedStoneToolMaterialsAreReservedDuringTableReturn() {
        for (String item : new String[]{"minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone"}) {
            assertTrue(SkillController.reserveForCrafting(item), item);
        }
    }

    @Test void ordinaryScaffoldingAndDifferentItemIdsRemainAvailable() {
        for (String item : new String[]{null, "minecraft:dirt", "minecraft:netherrack", "minecraft:gravel",
                "minecraft:cobblestone_stairs", "example:cobblestone"}) {
            assertFalse(SkillController.reserveForCrafting(item), String.valueOf(item));
        }
    }
}
