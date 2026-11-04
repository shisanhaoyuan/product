<script setup lang="ts">
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Cable,
  ChevronLeft,
  ChevronRight,
  Circle,
  CircleDashed,
  Copy,
  Database,
  Download,
  History,
  ImageOff,
  Layers,
  LayoutGrid,
  List,
  Package,
  Plus,
  RotateCcw,
  ScanLine,
  Search,
  ShieldCheck,
  Store,
  Upload,
} from '@lucide/vue';
const icons = {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Cable,
  ChevronLeft,
  ChevronRight,
  Circle,
  CircleDashed,
  Copy,
  Database,
  Download,
  History,
  ImageOff,
  Layers,
  LayoutGrid,
  List,
  Package,
  Plus,
  RotateCcw,
  ScanLine,
  Search,
  ShieldCheck,
  Store,
  Upload,
};
defineProps<{ name: string }>();
</script>
<template>
  <component
    :is="icons[name as keyof typeof icons] || icons.Circle"
    class="icon"
    aria-hidden="true"
    focusable="false"
  />
</template>
