<script setup>
import { computed } from 'vue'
import { Box, Text } from '@vue-tui/runtime'

const palette = {
  ' ': null,
  o: '#FFC000',
  y: '#F1EC14',
  g: '#85E1C9',
  b: '#4E95D9',
  p: '#7030A0'
}

const rows = computed(() => [
  'oo    oo   oooo   oooo   pppp    aa      ii',
  'oo   ooo  oo  oo oo  oo  pp pp  aaaa     ii',
  'oo  oo oo oo  oo oo  oo  pppp  aa  aa    ii',
  'oo oo  oo oo  oo oo  oo  pp    aaaaaa    ii',
  'oooo   oo  oooo   oooo   pp    aa  aa    ii',
  '   gggggg    bbbb    yyyyyy    gggggg      ',
  '  gggggggg  bbbbbb  yyyyyyyy  gggggggg     '
].map((line) => line.split('')))
</script>

<template>
  <Box flexDirection="column">
    <Box v-for="(row, rowIndex) in rows" :key="`logo-row-${rowIndex}`" flexDirection="row">
      <Text
        v-for="(cell, colIndex) in row"
        :key="`logo-cell-${rowIndex}-${colIndex}`"
        :backgroundColor="palette[cell] || undefined"
      >
        {{ '  ' }}
      </Text>
    </Box>
  </Box>
</template>
