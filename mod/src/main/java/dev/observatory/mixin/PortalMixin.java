package dev.observatory.mixin;

import dev.observatory.Observatory;
import net.minecraft.entity.Entity;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Entity.class)
public class PortalMixin {
    @Inject(method = "setInNetherPortal", at = @At("HEAD"))
    private void portalContact(BlockPos pos, CallbackInfo ci) {
        if ((Object) this instanceof ServerPlayerEntity player) Observatory.touchPortal(player);
    }
}
