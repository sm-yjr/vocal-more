// SPDX-License-Identifier: GPL-3.0-only
pub mod application;
pub mod billing;
pub mod catalog;
pub mod coach;
pub mod config;
pub mod dictionary;
pub mod history;
pub mod http;
pub mod learning;
pub mod learning_store;
pub mod migration;
pub mod observation;
pub mod persistence;
pub mod provider;
pub mod text;
pub mod wave;
pub mod workflow;

pub const PRODUCT_VERSION: &str = env!("VOCAL_MORE_PRODUCT_VERSION");
