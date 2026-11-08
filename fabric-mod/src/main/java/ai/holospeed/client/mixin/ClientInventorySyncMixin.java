package ai.holospeed.client.mixin;

import ai.holospeed.client.ServerInventorySync;
import ai.holospeed.client.ServerBlockSync;
import ai.holospeed.client.ServerSlotSync;
import net.minecraft.block.Block;
import net.minecraft.client.network.ClientPlayNetworkHandler;
import net.minecraft.network.packet.s2c.play.InventoryS2CPacket;
import net.minecraft.network.packet.s2c.play.BlockUpdateS2CPacket;
import net.minecraft.network.packet.s2c.play.ChunkDeltaUpdateS2CPacket;
import net.minecraft.network.packet.s2c.play.ScreenHandlerSlotUpdateS2CPacket;
import net.minecraft.registry.Registries;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ClientPlayNetworkHandler.class)
public abstract class ClientInventorySyncMixin {
    @Inject(method = "onInventory", at = @At("TAIL"))
    private void holospeed$inventoryApplied(InventoryS2CPacket packet, CallbackInfo callback) {
        ServerInventorySync.INSTANCE.record(this, packet.getSyncId());
        if (packet.getSyncId() == 0) {
            for (int slot = 36; slot < Math.min(45, packet.getContents().size()); slot++) {
                var stack = packet.getContents().get(slot);
                ServerSlotSync.INSTANCE.record(this, 0, slot, Registries.ITEM.getId(stack.getItem()).toString(), stack.getCount());
            }
        }
    }

    @Inject(method = "onScreenHandlerSlotUpdate", at = @At("TAIL"))
    private void holospeed$slotApplied(ScreenHandlerSlotUpdateS2CPacket packet, CallbackInfo callback) {
        var stack = packet.getStack();
        ServerSlotSync.INSTANCE.record(this, packet.getSyncId(), packet.getSlot(),
            Registries.ITEM.getId(stack.getItem()).toString(), stack.getCount());
    }

    @Inject(method = "onBlockUpdate", at = @At("TAIL"))
    private void holospeed$blockApplied(BlockUpdateS2CPacket packet, CallbackInfo callback) {
        ServerBlockSync.INSTANCE.record(this, packet.getPos().asLong(), Block.getRawIdFromState(packet.getState()));
    }

    @Inject(method = "onChunkDeltaUpdate", at = @At("TAIL"))
    private void holospeed$chunkBlocksApplied(ChunkDeltaUpdateS2CPacket packet, CallbackInfo callback) {
        packet.visitUpdates((position, state) -> ServerBlockSync.INSTANCE.record(this, position.asLong(), Block.getRawIdFromState(state)));
    }
}
