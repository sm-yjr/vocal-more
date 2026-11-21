// SPDX-License-Identifier: GPL-3.0-only
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const NAME_LIMIT: usize = 60;
pub const DESCRIPTION_LIMIT: usize = 400;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Scene {
    pub id: Uuid,
    pub name: String,
    pub description: String,
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct Scenes {
    pub entries: Vec<Scene>,
    pub active_id: Option<Uuid>,
}

impl Scenes {
    pub fn active(&self) -> Option<&Scene> {
        self.entries
            .iter()
            .find(|scene| Some(scene.id) == self.active_id)
    }

    pub(crate) fn validate(&self) -> Result<()> {
        let mut ids = std::collections::HashSet::new();
        for scene in &self.entries {
            validate_fields(&scene.name, &scene.description)?;
            ensure!(ids.insert(scene.id), "Duplicate dictation scene ID");
        }
        ensure!(
            self.active_id.is_none_or(|id| ids.contains(&id)),
            "Dictation scene no longer exists"
        );
        Ok(())
    }

    pub(crate) fn save(&mut self, id: Option<Uuid>, name: &str, description: &str) -> Result<()> {
        let name = name.trim();
        let description = description.trim();
        validate_fields(name, description)?;
        if let Some(id) = id {
            let scene = self
                .entries
                .iter_mut()
                .find(|scene| scene.id == id)
                .context("Dictation scene no longer exists")?;
            scene.name = name.into();
            scene.description = description.into();
        } else {
            self.entries.push(Scene {
                id: Uuid::new_v4(),
                name: name.into(),
                description: description.into(),
            });
        }
        Ok(())
    }

    pub(crate) fn delete(&mut self, id: Uuid) {
        self.entries.retain(|scene| scene.id != id);
        if self.active_id == Some(id) {
            self.active_id = None;
        }
    }
}

fn validate_fields(name: &str, description: &str) -> Result<()> {
    ensure!(
        !name.trim().is_empty()
            && name.chars().count() <= NAME_LIMIT
            && !name.contains(['\r', '\n']),
        "Scene name must be one line of 1–{NAME_LIMIT} characters"
    );
    ensure!(
        !description.trim().is_empty() && description.chars().count() <= DESCRIPTION_LIMIT,
        "Scene description must be 1–{DESCRIPTION_LIMIT} characters"
    );
    Ok(())
}
