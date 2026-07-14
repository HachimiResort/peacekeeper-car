import type { ShowCue, ShowScore } from "../api/types"

export interface ImportedShow {
  name: string
  score: ShowScore["score"]
}

type JsonRecord = Record<string, unknown>

export function parseShowImport(value: unknown, fallbackName = "导入演出"): ImportedShow {
  const root = record(value, "文件根节点必须是 JSON 对象")
  const score = record(root.score ?? root, "score 必须是 JSON 对象")
  const tracks = record(score.tracks, "缺少 tracks 轨道对象")
  const aliases = stringList(score.robot_ids ?? score.vehicles ?? Object.keys(tracks), "robot_ids 必须是车辆别名数组")
  if (!aliases.length) throw new Error("文件中至少需要一个车辆别名，例如 car_1")
  if (new Set(aliases).size !== aliases.length) throw new Error("车辆别名不能重复")
  const bpm = finiteNumber(score.bpm, "bpm")
  if (bpm < 40 || bpm > 240) throw new Error("bpm 必须在 40 到 240 之间")
  if ((score.beats_per_bar ?? 4) !== 4 || (score.ticks_per_beat ?? 2) !== 2) throw new Error("v1 只支持 4/4 拍和每拍 2 格")

  const parsedTracks: ShowScore["score"]["tracks"] = {}
  aliases.forEach(alias => {
    const source = record(tracks[alias] ?? {}, `${alias} 的轨道必须是对象`)
    parsedTracks[alias] = {
      motion: cueList(source.motion, `${alias}.motion`, true),
      lights: cueList(source.lights, `${alias}.lights`, false),
    }
  })
  return {
    name: typeof root.name === "string" && root.name.trim() ? root.name.trim() : fallbackName,
    score: {
      bpm,
      beats_per_bar: 4,
      ticks_per_beat: 2,
      robot_ids: aliases,
      tracks: parsedTracks,
      ...(typeof score.audio_name === "string" ? { audio_name: score.audio_name } : {}),
      ...(typeof score.audio_offset_ms === "number" ? { audio_offset_ms: score.audio_offset_ms } : {}),
    },
  }
}

export function mapImportedShow(imported: ImportedShow, mapping: Record<string, string>): ShowScore {
  const targets = imported.score.robot_ids.map(alias => mapping[alias])
  if (targets.some(id => !id)) throw new Error("请为每个车辆别名选择现场车辆")
  if (new Set(targets).size !== targets.length) throw new Error("同一辆现场车辆不能对应多个车辆别名")
  const tracks: ShowScore["score"]["tracks"] = {}
  imported.score.robot_ids.forEach((alias, index) => { tracks[targets[index]] = imported.score.tracks[alias] })
  return { id: "", name: imported.name, score: { ...imported.score, robot_ids: targets, tracks } }
}

function cueList(value: unknown, path: string, motion: boolean): ShowCue[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) throw new Error(`${path} 必须是数组`)
  const cues = value.map((item, index) => {
    const cue = record(item, `${path}[${index}] 必须是对象`)
    const atTick = integer(cue.at_tick, `${path}[${index}].at_tick`, 0)
    const duration = integer(cue.duration_ticks, `${path}[${index}].duration_ticks`, 1)
    return motion ? {
      at_tick: atTick,
      duration_ticks: duration,
      linear_x: optionalNumber(cue.linear_x),
      linear_y: optionalNumber(cue.linear_y),
      angular_z: optionalNumber(cue.angular_z),
    } : {
      at_tick: atTick,
      duration_ticks: duration,
      effect: typeof cue.effect === "string" ? cue.effect : "off",
    }
  })
  const ordered = [...cues].sort((a, b) => a.at_tick - b.at_tick)
  ordered.forEach((cue, index) => { if (index && cue.at_tick < ordered[index - 1].at_tick + ordered[index - 1].duration_ticks) throw new Error(`${path} 中的动作不能重叠`) })
  return cues
}

function record(value: unknown, message: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(message)
  return value as JsonRecord
}
function stringList(value: unknown, message: string) {
  if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim())) throw new Error(message)
  return value.map(item => String(item).trim())
}
function finiteNumber(value: unknown, name: string) {
  const result = Number(value)
  if (!Number.isFinite(result)) throw new Error(`${name} 必须是数字`)
  return result
}
function optionalNumber(value: unknown) { return value === undefined ? 0 : finiteNumber(value, "速度") }
function integer(value: unknown, name: string, minimum: number) {
  const result = finiteNumber(value, name)
  if (!Number.isInteger(result) || result < minimum) throw new Error(`${name} 必须是不小于 ${minimum} 的整数`)
  return result
}
