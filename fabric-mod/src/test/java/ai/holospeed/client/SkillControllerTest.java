package ai.holospeed.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** Request and progress policy only: no client, world, network, or Baritone startup. */
class SkillControllerTest {
    private static JsonObject json(String source) {
        return JsonParser.parseString(source).getAsJsonObject();
    }

    private static JsonObject mine() {
        return json("{\"skill\":\"mine\",\"item\":\"oak_log\"}");
    }

    @Test
    void miningRequestsExpressAnInventoryTargetAndHaveBoundedDefaults() {
        JsonObject input = json("""
                {"type":"skill","id":"mine-opening-logs","skill":"mine",
                 "item":"oak_log","target_count":12}
                """);
        JsonObject original = input.deepCopy();
        SkillController.SkillRequest request = SkillController.parseRequest(input);

        assertAll(
                () -> assertEquals("mine", request.kind()),
                () -> assertEquals("minecraft:oak_log", request.item()),
                () -> assertEquals(12, request.targetCount()),
                () -> assertEquals(1200, request.maxTicks()),
                () -> assertEquals(request, SkillController.parseRequest(input)),
                () -> assertEquals(original, input)
        );
        assertEquals(1, SkillController.parseRequest(mine()).targetCount());
    }

    @Test
    void namespacedCraftingRequestsRetainTheExactRecipeItem() {
        SkillController.SkillRequest request = SkillController.parseRequest(json("""
                {"skill":"craft","item":"minecraft:wooden_pickaxe",
                 "target_count":1,"max_ticks":200}
                """));
        assertAll(
                () -> assertEquals("craft", request.kind()),
                () -> assertEquals("minecraft:wooden_pickaxe", request.item()),
                () -> assertEquals(1, request.targetCount()),
                () -> assertEquals(200, request.maxTicks())
        );
    }

    @Test
    void earlyGameMineAndCraftTargetsUseTheirSeparateAllowlists() {
        for (String item : new String[]{"birch_log", "cherry_log", "stone", "cobblestone",
                "deepslate", "cobbled_deepslate"}) {
            JsonObject input = mine();
            input.addProperty("item", item);
            assertEquals("minecraft:" + item, SkillController.parseRequest(input).item());
        }
        for (String item : new String[]{"birch_planks", "mangrove_planks", "stick",
                "crafting_table", "wooden_pickaxe", "stone_pickaxe", "stone_axe"}) {
            JsonObject input = json("{\"skill\":\"craft\"}");
            input.addProperty("item", item);
            assertEquals("minecraft:" + item, SkillController.parseRequest(input).item());
        }
        assertThrows(IllegalArgumentException.class,
                () -> SkillController.parseRequest(json("{\"skill\":\"mine\",\"item\":\"wooden_pickaxe\"}")));
        assertThrows(IllegalArgumentException.class,
                () -> SkillController.parseRequest(json("{\"skill\":\"craft\",\"item\":\"oak_log\"}")));
    }

    @Test
    void normalMiningSmeltingAndRecipesHaveSeparateExactTargets() {
        for (String item : new String[]{"raw_iron", "coal", "flint", "dirt"}) {
            JsonObject input = mine(); input.addProperty("item", item);
            assertEquals("minecraft:" + item, SkillController.parseRequest(input).item());
        }
        for (String item : new String[]{"furnace", "bucket", "flint_and_steel"}) {
            JsonObject input = json("{\"skill\":\"craft\"}"); input.addProperty("item", item);
            assertEquals("minecraft:" + item, SkillController.parseRequest(input).item());
        }
        assertEquals("smelt", SkillController.parseRequest(json("{\"skill\":\"smelt\",\"item\":\"iron_ingot\",\"target_count\":4}")).kind());
        for (String source : new String[]{"{\"skill\":\"mine\",\"item\":\"iron_ingot\"}",
            "{\"skill\":\"smelt\",\"item\":\"gold_ingot\"}", "{\"skill\":\"craft\",\"item\":\"iron_ingot\"}"})
            assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(json(source)));
    }

    @Test
    void exactTargetAndDeadlineLimitsAreAllowed() {
        for (int count : new int[]{1, 64}) {
            for (int ticks : new int[]{20, 1800}) {
                JsonObject input = mine();
                input.addProperty("target_count", count);
                input.addProperty("max_ticks", ticks);
                SkillController.SkillRequest request = SkillController.parseRequest(input);
                assertEquals(count, request.targetCount());
                assertEquals(ticks, request.maxTicks());
            }
        }
    }

    @Test
    void fractionalUnboundedAndNonfiniteTargetsAreRejected() {
        for (double count : new double[]{-1, 0, 65, 1000000, 1.5,
                Double.NaN, Double.NEGATIVE_INFINITY, Double.POSITIVE_INFINITY}) {
            JsonObject input = mine();
            input.addProperty("target_count", count);
            assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(input),
                    "target_count=" + count);
        }
        for (double ticks : new double[]{-1, 0, 19, 1801, 1000000, 20.5,
                Double.NaN, Double.NEGATIVE_INFINITY, Double.POSITIVE_INFINITY}) {
            JsonObject input = mine();
            input.addProperty("max_ticks", ticks);
            assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(input),
                    "max_ticks=" + ticks);
        }
    }

    @Test
    void numericStringsBooleansAndNullsAreNotCoercedIntoLimits() {
        for (String field : new String[]{"target_count", "max_ticks"}) {
            for (String value : new String[]{"\"64\"", "true", "false", "null", "[]", "{}"}) {
                JsonObject input = mine();
                input.add(field, JsonParser.parseString(value));
                assertThrows(IllegalArgumentException.class,
                        () -> SkillController.parseRequest(input), field + "=" + value);
            }
        }
    }

    @Test
    void skillAndItemAreRequiredNonemptyStrings() {
        for (String source : new String[]{
                "{}", "{\"skill\":\"mine\"}", "{\"item\":\"oak_log\"}",
                "{\"skill\":null,\"item\":\"oak_log\"}",
                "{\"skill\":true,\"item\":\"oak_log\"}",
                "{\"skill\":\"\",\"item\":\"oak_log\"}",
                "{\"skill\":\"mine\",\"item\":null}",
                "{\"skill\":\"mine\",\"item\":42}",
                "{\"skill\":\"mine\",\"item\":[]}",
                "{\"skill\":\"mine\",\"item\":\"\"}"}) {
            assertThrows(IllegalArgumentException.class,
                    () -> SkillController.parseRequest(json(source)), source);
        }
    }

    @Test
    void unsupportedSkillsCannotBecomeGameCommandsOrUnboundedActions() {
        for (String skill : new String[]{"navigate", "give", "teleport", "command", "mineByName",
                "/give @s diamond", "mine; stop", "#mine diamond_ore"}) {
            JsonObject input = mine();
            input.addProperty("skill", skill);
            assertThrows(IllegalArgumentException.class,
                    () -> SkillController.parseRequest(input), skill);
        }
    }

    @Test
    void crossNamespaceAndCommandLikeTargetsCannotReachBaritone() {
        for (String item : new String[]{"mod:oak_log", "minecraft:diamond_ore", "minecraft:bedrock",
                "minecraft:oak_log;give diamond", "oak_log #goto 0 0", "#minecraft:logs",
                "minecraft:../../diamond", "minecraft:oak_log,minecraft:diamond_ore", "*"}) {
            JsonObject input = mine();
            input.addProperty("item", item);
            assertThrows(IllegalArgumentException.class,
                    () -> SkillController.parseRequest(input), item);
        }
    }

    @Test
    void unknownAndLegacyFieldsCannotSmuggleAlternativeInstructions() {
        for (String field : new String[]{"command", "commands", "count", "additional_count",
                "target", "blocks", "path", "force", "creative", "teleport", "repeat"}) {
            JsonObject input = mine();
            input.addProperty(field, "/give @s diamond 64");
            assertThrows(IllegalArgumentException.class,
                    () -> SkillController.parseRequest(input), field);
        }
    }

    @Test
    void progressWatchEventuallyStopsAnIdleOrLosingInventorySkill() {
        SkillController.ProgressWatch watch = new SkillController.ProgressWatch(3, 0, 64, 0);
        assertFalse(watch.stalled(399));
        watch.observe(399, 2, 0, 64, 0);
        assertTrue(watch.stalled(400));
    }

    @Test
    void newlyAcquiredItemsResetTheProgressDeadline() {
        SkillController.ProgressWatch watch = new SkillController.ProgressWatch(3, 0, 64, 0);
        watch.observe(300, 4, 0, 64, 0);
        assertFalse(watch.stalled(699));
        assertTrue(watch.stalled(700));
    }

    @Test
    void meaningfulMovementResetsTheDeadlineButSubthresholdJitterDoesNot() {
        SkillController.ProgressWatch watch = new SkillController.ProgressWatch(0, 0, 64, 0);
        watch.observe(300, 0, 0.1, 64, 0);
        assertTrue(watch.stalled(400));
        watch.observe(450, 0, 0.25, 64, 0);
        assertFalse(watch.stalled(849));
        assertTrue(watch.stalled(850));
    }
}
