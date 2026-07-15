#!/usr/bin/env python3
"""
Script para limpar COMPLETAMENTE a GPU antes de carregar o modelo.
Execute este script ANTES de rodar o app.
"""

import os
import gc
import torch
import subprocess
import time

print("="*70)
print("🧹 LIMPEZA TOTAL DA GPU")
print("="*70)

# 1. Verificar processos usando GPU
print("\n Processos usando GPU:")
try:
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
        capture_output=True,
        text=True
    )
    
    if result.stdout.strip():
        print(result.stdout)
        print("\n⚠️  ATENÇÃO: Há processos usando a GPU!")
        print("   Considere matar esses processos antes de continuar.")
    else:
        print("   ✅ Nenhum processo usando GPU")
        
except Exception as e:
    print(f"   ⚠️  Não foi possível verificar: {e}")

# 2. Limpar cache Python/PyTorch se já estiver carregado
print("\n🧹 Limpando cache do PyTorch...")
if torch.cuda.is_available():
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    for _ in range(3):
        gc.collect()
    print("   ✅ Cache limpo")
else:
    print("   ⚠️  CUDA não disponível!")

# 3. Limpar cache do sistema
print("\n🧹 Limpando cache do sistema...")
try:
    subprocess.run(["sync"], check=False)
    time.sleep(1)
    print("   ✅ Cache do sistema limpo")
except:
    print("   ⚠️  Não foi possível limpar cache do sistema")

# 4. Estatísticas finais
print("\n📊 Estatísticas de VRAM:")
if torch.cuda.is_available():
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    allocated = torch.cuda.memory_allocated(0) / 1024**3
    reserved = torch.cuda.memory_reserved(0) / 1024**3
    free = total - allocated
    
    print(f"   Total:     {total:.2f} GB")
    print(f"   Alocada:   {allocated:.2f} GB")
    print(f"   Reservada: {reserved:.2f} GB")
    print(f"   Livre:     {free:.2f} GB")
    
    if allocated > 0.5:
        print(f"\n   ⚠️  ALERTA: {allocated:.2f} GB ainda alocados!")
        print("   Reinicie o Python ou mate processos usando GPU")
    else:
        print("\n   ✅ GPU praticamente limpa!")
else:
    print("   ❌ CUDA não disponível")

print("\n" + "="*70)
print("✅ LIMPEZA CONCLUÍDA")
print("="*70)
print("\nAgora você pode rodar:")
print("  python app_gemma_2_9B.py")
print("="*70)
