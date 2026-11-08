package ai.holospeed.client.mixin;

import ai.holospeed.client.HoloSpeedClient;
import net.minecraft.client.render.GameRenderer;
import net.minecraft.client.render.RenderTickCounter;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(GameRenderer.class)
public abstract class GameRendererMixin {
    // Vanilla has flushed all GUI batches here but has not restored the GUI model-view yet.
    @Inject(method = "render", at = @At(value = "INVOKE", target = "Lorg/joml/Matrix4fStack;popMatrix()Lorg/joml/Matrix4fStack;", shift = At.Shift.BEFORE, remap = false))
    private void holospeed$afterGui(RenderTickCounter counter, boolean tick, CallbackInfo ci) {
        HoloSpeedClient.renderTail();
    }
}
