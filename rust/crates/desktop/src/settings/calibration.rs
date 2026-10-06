// SPDX-License-Identifier: GPL-3.0-only
//! Guided calibration keeps the same post-gain RMS semantics as the old UI.
use super::schema::get;
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Quiet,
    Whisper,
}
impl Phase {
    pub fn duration_ms(self) -> u64 {
        match self {
            Self::Quiet => 3000,
            Self::Whisper => 4500,
        }
    }
}
#[derive(Clone, Debug, PartialEq)]
pub struct Recommendation {
    pub noise: f64,
    pub whisper: f64,
    pub gain: f64,
    pub ceiling: f64,
    pub clamped: bool,
}
#[derive(Clone, Debug, Default)]
pub struct Calibration {
    pub open: bool,
    pub phase: Option<Phase>,
    pub starting: bool,
    pub stopping: bool,
    pub epoch: u64,
    pub quiet: Vec<f64>,
    pub whisper: Vec<f64>,
    pub result: Option<Result<Recommendation, &'static str>>,
    pub measurement_gain: f64,
}
impl Calibration {
    pub fn begin(&mut self, config: &Value) {
        self.epoch += 1;
        self.phase = Some(Phase::Quiet);
        self.starting = true;
        self.stopping = false;
        self.quiet.clear();
        self.whisper.clear();
        self.result = None;
        self.measurement_gain = if get(config, "audio.gain_mode") == "manual" {
            get(config, "audio.gain").as_f64().unwrap_or(1.).max(0.001)
        } else {
            1.
        };
    }
    pub fn sample(&mut self, rms: f64) {
        if self.starting || self.stopping || !rms.is_finite() || rms <= 0. {
            return;
        }
        match self.phase {
            Some(Phase::Quiet) => self.quiet.push(rms),
            Some(Phase::Whisper) => self.whisper.push(rms),
            None => {}
        }
    }
    /// Returns true when the next phase needs a fresh backend microphone take.
    pub fn complete(&mut self) -> bool {
        self.starting = false;
        self.stopping = false;
        match self.phase {
            Some(Phase::Quiet) => {
                self.phase = Some(Phase::Whisper);
                self.starting = true;
                true
            }
            Some(Phase::Whisper) => {
                self.phase = None;
                self.result = Some(recommend(&self.quiet, &self.whisper, self.measurement_gain));
                false
            }
            None => false,
        }
    }
    pub fn close(&mut self) {
        self.epoch += 1;
        self.open = false;
        self.phase = None;
        self.starting = false;
        self.stopping = false;
    }
}
pub fn dbfs(rms: f64) -> f64 {
    if rms.is_finite() && rms > 0. {
        20. * rms.log10()
    } else {
        f64::NEG_INFINITY
    }
}
pub fn waveform(rms: f64, ceiling: f64) -> f32 {
    ((dbfs(rms) + 60.) / (ceiling.clamp(-30., 0.) + 60.)).clamp(0., 1.) as f32
}
pub fn percentile(samples: &[f64], percentile: f64) -> Option<f64> {
    let mut values = samples
        .iter()
        .copied()
        .map(dbfs)
        .filter(|v| v.is_finite())
        .collect::<Vec<_>>();
    values.sort_by(f64::total_cmp);
    if values.is_empty() {
        return None;
    }
    let rank = (values.len() - 1) as f64 * percentile.clamp(0., 1.);
    let lower = rank.floor() as usize;
    let upper = rank.ceil() as usize;
    Some(values[lower] * (1. - rank.fract()) + values[upper] * rank.fract())
}
pub fn recommend(
    quiet: &[f64],
    whisper: &[f64],
    gain: f64,
) -> Result<Recommendation, &'static str> {
    if quiet.len() < 8 || whisper.len() < 8 {
        return Err("insufficient-samples");
    }
    let noise = percentile(quiet, 0.5).ok_or("insufficient-samples")?;
    let whisper = percentile(whisper, 0.9).ok_or("insufficient-samples")?;
    if whisper < noise + 6. {
        return Err("low-snr");
    }
    let gain = gain.max(0.001);
    let raw = gain * 10_f64.powf((-14. - whisper) / 20.);
    let recommended = (raw.clamp(1., 50.) * 100.).round() / 100.;
    let ceiling = (whisper + 20. * (recommended / gain).log10() + 2.)
        .round()
        .clamp(-30., 0.);
    Ok(Recommendation {
        noise,
        whisper,
        gain: recommended,
        ceiling,
        clamped: !(1. ..=50.).contains(&raw),
    })
}
pub fn changes(result: &Recommendation, config: &Value) -> Vec<(&'static str, Value)> {
    let mut changes = vec![
        ("audio.gain_mode", json!("manual")),
        ("audio.gain", json!(result.gain)),
        ("audio.highpass_filter", json!(true)),
    ];
    if get(config, "audio.highpass_freq").as_f64().unwrap_or(200.) < 220. {
        changes.push(("audio.highpass_freq", json!(220)));
    }
    changes.push(("audio.waveform_ceiling_dbfs", json!(result.ceiling)));
    if get(config, "audio.soft_limiter") == false {
        changes.push(("audio.soft_limiter", json!(true)));
    }
    changes
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recommendation_rescales_post_gain_measurement() {
        let result = recommend(&[0.001; 12], &[0.02; 12], 4.).unwrap();
        assert!((result.gain - 39.91).abs() < 0.01);
        assert_eq!(result.ceiling, -12.);
        let boosted = recommend(&[0.002; 12], &[0.04; 12], 8.).unwrap();
        assert_eq!(result.gain, boosted.gain);
    }
    #[test]
    fn low_snr_and_missing_samples_cannot_apply() {
        assert_eq!(recommend(&[0.01; 12], &[0.012; 12], 1.), Err("low-snr"));
        assert_eq!(
            recommend(&[0.001; 7], &[0.1; 12], 1.),
            Err("insufficient-samples")
        );
    }
    #[test]
    fn ordered_config_changes_preserve_stronger_filter() {
        let result = recommend(&[0.001; 12], &[0.02; 12], 4.).unwrap();
        let changes = changes(
            &result,
            &json!({"audio":{"highpass_freq":280,"soft_limiter":true}}),
        );
        assert_eq!(changes[0], ("audio.gain_mode", json!("manual")));
        assert!(
            !changes
                .iter()
                .any(|(key, _)| *key == "audio.highpass_freq" || *key == "audio.soft_limiter")
        );
    }
    #[test]
    fn lifecycle_stops_measurement_before_phase_boundary() {
        let mut state = Calibration {
            open: true,
            ..Default::default()
        };
        state.begin(&json!({"audio":{"gain_mode":"automatic","gain":8}}));
        assert_eq!(state.measurement_gain, 1.);
        state.sample(0.1);
        assert!(state.quiet.is_empty());
        state.starting = false;
        state.sample(0.001);
        assert_eq!(state.quiet.len(), 1);
        state.stopping = true;
        state.sample(0.2);
        assert_eq!(state.quiet.len(), 1);
        assert!(state.complete());
        assert_eq!(state.phase, Some(Phase::Whisper));
        let epoch = state.epoch;
        state.close();
        assert!(state.epoch > epoch);
        assert_eq!(state.phase, None);
    }
}
