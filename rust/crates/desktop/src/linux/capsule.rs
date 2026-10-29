// SPDX-License-Identifier: GPL-3.0-only
//! Focus-preserving X11 popup. GNOME Wayland uses the companion Shell actor.
use crate::bridge::CommandSink;
use gpui_kit::*;
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Model {
    pub active: bool,
    pub state: String,
    pub text: String,
    pub level: f32,
    pub english: bool,
    pub reduced_motion: bool,
}
impl Default for Model {
    fn default() -> Self {
        Self {
            active: false,
            state: "hidden".into(),
            text: String::new(),
            level: 0.,
            english: false,
            reduced_motion: false,
        }
    }
}
impl Model {
    pub fn title(&self) -> &str {
        match (self.state.as_str(), self.english) {
            ("starting", true) => "Preparing microphone",
            ("starting", false) => "正在连接麦克风",
            ("recording", true) => "Listening",
            ("recording", false) => "正在聆听",
            ("processing" | "cancelling", true) => "Processing",
            ("processing" | "cancelling", false) => "正在处理",
            ("error", true) => "Try again",
            ("error", false) => "请重试",
            _ if self.english => "Vocal More",
            _ => "Vocal More",
        }
    }
    pub fn bound(&mut self) {
        self.text = self.text.chars().take(200).collect();
        self.level = if self.level.is_finite() {
            self.level.clamp(0., 1.)
        } else {
            0.
        };
    }
}
pub struct View {
    pub model: Model,
    commands: CommandSink,
    smoothed: f32,
    phase: f32,
}
impl View {
    fn new(model: Model, commands: CommandSink) -> Self {
        Self {
            model,
            commands,
            smoothed: 0.,
            phase: 0.,
        }
    }
    pub fn tick(&mut self, cx: &mut Context<Self>) {
        let target = self.model.level;
        let rate = if target > self.smoothed { 0.65 } else { 0.13 };
        self.smoothed += (target - self.smoothed) * rate;
        self.phase += if self.model.reduced_motion { 0. } else { 0.13 };
        cx.notify();
    }
}
pub fn open(
    model: Model,
    commands: CommandSink,
    cx: &mut App,
) -> anyhow::Result<(AnyWindowHandle, Entity<View>)> {
    let bounds = cx
        .primary_display()
        .map(|display| display.bounds())
        .unwrap_or(Bounds::new(
            point(px(0.), px(0.)),
            size(px(1280.), px(720.)),
        ));
    let origin = point(
        bounds.origin.x + (bounds.size.width - px(420.)) / 2.,
        bounds.origin.y + bounds.size.height - px(130.),
    );
    let mut view = None;
    let window = cx.open_window(
        WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(Bounds::new(
                origin,
                size(px(420.), px(96.)),
            ))),
            titlebar: None,
            focus: false,
            kind: WindowKind::PopUp,
            is_resizable: false,
            is_minimizable: false,
            window_background: WindowBackgroundAppearance::Transparent,
            app_id: Some("io.github.sm_yjr.VocalMore.Capsule".into()),
            ..Default::default()
        },
        |window, cx| {
            window.set_rem_size(px(14.));
            let entity = cx.new(|_| View::new(model, commands));
            view = Some(entity.clone());
            entity
        },
    )?;
    Ok((window.into(), view.expect("window builder ran")))
}
impl Render for View {
    fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
        let cancel = self.commands.clone();
        let finish = self.commands.clone();
        let bars = (0..21).map(|i| {
            let center = (i as f32 - 10.) / 6.;
            let envelope = (-center * center / 2.).exp();
            let motion = if self.model.reduced_motion {
                0.8
            } else {
                0.65 + 0.35 * (self.phase + i as f32 * 0.8).sin().abs()
            };
            div()
                .w(px(3.))
                .h(px(3. + 27. * self.smoothed * envelope * motion))
                .rounded_full()
                .bg(rgba(0xffffffcc))
        });
        let text = if self.model.text.is_empty() {
            self.model.title().to_owned()
        } else {
            self.model.text.clone()
        };
        div().size_full().p_2().child(
            div()
                .size_full()
                .rounded_full()
                .bg(rgb(0x000000))
                .border_1()
                .border_color(rgba(0xffffff52))
                .text_color(rgba(0xffffffe6))
                .font_family("sans-serif")
                .px_4()
                .py_3()
                .flex()
                .items_center()
                .gap_3()
                .child(
                    div()
                        .w(px(88.))
                        .h(px(34.))
                        .flex()
                        .items_center()
                        .justify_center()
                        .gap(px(2.))
                        .children(bars),
                )
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .text_sm()
                        .overflow_hidden()
                        .child(text),
                )
                .child(
                    div()
                        .id("capsule-cancel")
                        .cursor_pointer()
                        .px_2()
                        .text_color(rgb(0xff3b30))
                        .child("×")
                        .on_click(move |_, _, _| {
                            cancel.request("cancel", json!({}));
                        }),
                )
                .child(
                    div()
                        .id("capsule-finish")
                        .cursor_pointer()
                        .px_2()
                        .text_color(rgb(0x34c759))
                        .child("✓")
                        .on_click(move |_, _, _| {
                            finish.request("stop", json!({}));
                        }),
                ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn capsule_bounds_untrusted_text_and_nonfinite_audio() {
        let mut model = Model {
            text: "文".repeat(1000),
            level: f32::NAN,
            ..Default::default()
        };
        model.bound();
        assert_eq!(model.text.chars().count(), 200);
        assert_eq!(model.level, 0.);
    }
}
