package ai.holospeed.client.mixin;

import net.minecraft.client.gui.screen.ingame.HandledScreen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Read-only container origins for interpreting vanilla slot coordinates. */
@Mixin(HandledScreen.class)
public interface HandledScreenAccessor {
    @Accessor("x") int holospeed$getOriginX();
    @Accessor("y") int holospeed$getOriginY();
}
