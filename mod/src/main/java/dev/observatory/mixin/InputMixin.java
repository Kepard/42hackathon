package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.client.util.InputUtil;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(InputUtil.class)
public class InputMixin {
    @Inject(method = "isKeyPressed", at = @At("HEAD"), cancellable = true)
    private static void syntheticShift(long window, int key, CallbackInfoReturnable<Boolean> ci) {
        if ((key == 340 || key == 344) && Observatory.isShiftHeld()) ci.setReturnValue(true);
    }
}
