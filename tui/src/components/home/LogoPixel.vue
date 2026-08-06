<script setup>
import { TText, TView } from '@simon_he/vue-tui'

const props = defineProps({
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 }
})

const palette = {
  ' ': null,
  o: '#FFC000',
  y: '#F1EC14',
  g: '#85E1C9',
  b: '#4E95D9',
  p: '#7030A0'
}

const rows = [
  'oo    oo   oooo   oooo   pppp    aa      ii',
  'oo   ooo  oo  oo oo  oo  pp pp  aaaa     ii',
  'oo  oo oo oo  oo oo  oo  pppp  aa  aa    ii',
  'oo oo  oo oo  oo oo  oo  pp    aaaaaa    ii',
  'oooo   oo  oooo   oooo   pp    aa  aa    ii',
  '   gggggg    bbbb    yyyyyy    gggggg      ',
  '  gggggggg  bbbbbb  yyyyyyyy  gggggggg     '
].map((line) => line.split(''))

const cells = rows.flatMap((row, rowIndex) =>
  row.map((cell, colIndex) => ({
    key: `${rowIndex}-${colIndex}`,
    x: colIndex * 2,
    y: rowIndex,
    color: palette[cell] || null
  }))
)

const width = Math.max(...rows.map((row) => row.length)) * 2
const height = rows.length
</script>

<template>
  <TView :x="props.x" :y="props.y" :w="width" :h="height">
    <TText
      v-for="cell in cells"
      :key="cell.key"
      :x="cell.x"
      :y="cell.y"
      :w="2"
      value="  "
      :style="cell.color ? { bg: cell.color } : undefined"
    />
  </TView>
</template>
