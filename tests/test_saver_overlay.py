"""Run the actual saver lifecycle functions with host labels, without LVGL or an ESPHome board build."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STUBS = r'''
#include "components/smart_display/saver_view.h"
#include "components/smart_display/camera_view.h"
#include "components/smart_display/picture_loader.h"
#include <array>
#include <cassert>
#include <functional>
#include <memory>
#include <utility>
#include <vector>
#define ESP_LOGI(...) ((void) 0)
struct lv_obj_t { std::string text; };
struct lv_font_t {};
struct lv_point_t { int y = 24; };
struct lv_image_dsc_t { struct { int w = 480, h = 480; } header; };
constexpr int LV_TEXT_ALIGN_LEFT = 0, LV_TEXT_FLAG_NONE = 0, LV_OBJ_FLAG_HIDDEN = 0, LV_IMAGE_ALIGN_COVER = 0;
std::vector<std::unique_ptr<lv_obj_t>> objects;
int created = 0, deleted = 0;
lv_obj_t *make(const std::string &text = "") {
  objects.emplace_back(new lv_obj_t{text}); ++created; return objects.back().get();
}
void lv_obj_delete(lv_obj_t *) { ++deleted; }
void lv_obj_set_pos(lv_obj_t *, int, int) {}
void lv_obj_set_size(lv_obj_t *, int, int) {}
void lv_obj_add_flag(lv_obj_t *, int) {}
void lv_obj_move_foreground(lv_obj_t *) {}
void lv_image_set_inner_align(lv_obj_t *, int) {}
int lv_pct(int n) { return n; }
int lv_font_get_line_height(const lv_font_t *) { return 24; }
void lv_text_get_size(lv_point_t *, const char *, const lv_font_t *, int, int, int, int) {}
void set_font(lv_obj_t *, const lv_font_t *) {}
void label(lv_obj_t *o, const std::string &text) { o->text = text; }
lv_obj_t *saver_text(lv_obj_t *, const lv_font_t *, uint32_t, const media_card::Rect &, const std::string &text, int) {
  return make(text);
}
namespace header_bar { struct Item { bool operator==(const Item &) const { return true; } }; }
namespace overlay_card { int screen_width() { return 480; } int screen_height() { return 480; } }
namespace theme { uint32_t mix(uint32_t ink, int, int) { return ink; } }
namespace live_view { using Handle = int; void close(Handle) {} }
namespace esphome { struct ESPTime { int second = 0; }; }
esphome::ESPTime clock_now;
std::function<esphome::ESPTime()> now_time = [] { return clock_now; };
std::string time_text(esphome::ESPTime now) { return std::to_string(now.second); }
std::string date_text(esphome::ESPTime now) { return std::to_string(now.second / 86400); }
std::string am_pm(esphome::ESPTime) { return ""; }
lv_obj_t *saver_root = nullptr, *saver_first = nullptr, *saver_second = nullptr;
bool saver_lit = true, saver_camera = true, saver_words_due = false, saver_clock_overlay = false;
bool saver_follow_due = false, saver_pictures = false, saver_woke = false;
uint32_t saver_ticked = 0, saver_flipped = 0;
constexpr uint32_t SAVER_CAMERA_MS = 15000;
std::array<lv_obj_t *, 3> saver_keys{};
const lv_font_t *watch_font = nullptr, *detail_font = nullptr, *control_font = nullptr;
lv_obj_t *camera_root = nullptr, *camera_picture = nullptr, *camera_note = nullptr, *camera_back = nullptr;
lv_obj_t *camera_title = nullptr, *camera_spinner = nullptr, *camera_sound_key = nullptr, *map_card_obj = nullptr;
lv_obj_t *map_zoom_keys = nullptr, *map_pad = nullptr, *map_waiting = nullptr;
int camera_map_index = -1;
std::string map_focus;
bool map_pinned = false, camera_release_due = false;
struct MapMove {} map_move, map_move_shown;
struct MapSheet {} map_sheet;
namespace owners { const picture_loader::Owner VIEW{picture_loader::Kind::VIEW, 0}; }
picture_loader::Loader loader;
bool saver_wanted() { return false; }
void saver_show() {}
void saver_faces() {}
void saver_keys_draw() {}
uint32_t media_ink() { return 0; }
saver_view::Keys saver_key_layout() { return {}; }
bool shows(picture_loader::Outcome o) {
  return o == picture_loader::Outcome::LOADED || o == picture_loader::Outcome::UNCHANGED;
}
std::string camera_key() { return ""; }
lv_image_dsc_t *picture_of(const std::string &) { static lv_image_dsc_t picture; return &picture; }
void picture_memory(const char *, const char *, int) {}
void camera_show(lv_obj_t *, lv_obj_t *&picture, lv_image_dsc_t *, bool) { if (!picture) picture = make(); }
void camera_note_text(const std::string &) {}
void map_keys_place() {}
void map_preview() {}
void map_waiting_show() {}
const char *tr(int) { return ""; }
namespace txt { constexpr int camera_no_image = 0; }
void saver_words();
void saver_follow_pending();
'''

SCENARIOS = r'''
void start(const std::string &kind, bool clock = false) {
  camera_root = make();
  saver_now.kind = kind; saver_now.clock = clock; saver_now.entity = "camera.album"; saver_now.picture = "frame1";
  saver_now.name = "Camera"; saver_now.title = "Track"; saver_now.artist = "Artist";
  saver_next = saver_now;
  clock_now.second = 86399;
}
void tick(uint32_t now, int second) { clock_now.second = second; saver_tick(now); }
void next(const std::string &kind, bool clock, const std::string &revision) {
  saver_next = saver_now;
  saver_next.kind = kind; saver_next.clock = clock; saver_next.picture = revision;
  saver_follow();
}
void outage() {
  start("slideshow", true);
  auto round = [](uint32_t now) {
    picture_loader::Want want;
    want.key = saver_now.picture; want.tag = saver_now.entity;
    want.rank = picture_loader::Rank::VIEW; want.slot = picture_loader::Slot::FULL; want.every = camera.every;
    want.ask = [] {}; want.done = view_done;
    loader.begin(); loader.want(owners::VIEW, want); loader.end(now, true);
  };
  auto finish = [](bool ok, uint32_t now) {
    assert(loader.busy());
    const auto key = loader.loaded(picture_loader::Slot::FULL, ok, now);
    if (!key.empty()) loader.arrived(key, false);
  };
  saver_tick(1000);
  assert(!saver_first && !saver_second);
  round(1000);
  loader.answer(saver_now.entity, "http://app/frame1", 1100);
  round(1200);
  finish(true, 1400);
  auto *first = saver_first, *second = saver_second, *photo = camera_picture;
  const int initial_created = created, initial_deleted = deleted;
  next("slideshow", true, "frame2");
  assert(saver_words_due);
  round(1500);
  loader.answer(saver_now.entity, "http://app/frame2", 1600);
  round(1700);
  finish(false, 1800);
  tick(2000, 86400);
  assert(first->text == "86400" && second->text == "1");
  round(2800); finish(false, 2900);
  tick(3000, 86401);
  assert(first->text == "86401" && second->text == "1");
  round(3900); finish(false, 4000);
  round(5000);
  loader.answer(saver_now.entity, "", 5100);
  tick(6000, 86402);
  assert(first->text == "86402" && second->text == "1");
  assert(saver_words_due && camera_picture == photo);
  assert(created == initial_created && deleted == initial_deleted);
  round(15200);
  loader.answer(saver_now.entity, "http://app/recovered", 15300);
  round(15400);
  finish(true, 15500);
  tick(16000, 86403);
  assert(!saver_words_due && saver_first == first && saver_second == second);
  assert(first->text == "86403" && created == initial_created && deleted == initial_deleted);
}
void transition(const std::string &kind) {
  start(kind);
  view_done(picture_loader::Outcome::LOADED);
  auto *old_first = saver_first;
  const auto old_text = old_first->text;
  next("slideshow", true, "frame2");
  view_done(picture_loader::Outcome::FAILED);
  tick(1000, 86400);
  assert(old_first->text == old_text);
  view_done(picture_loader::Outcome::LOADED);
  assert(saver_first->text == "86400" && saver_second->text == "1");
  auto *first = saver_first, *second = saver_second;
  next(kind, false, "frame3");
  view_done(picture_loader::Outcome::NONE);
  tick(2000, 86401);
  assert(first->text == "86401" && second->text == "1");
  view_done(picture_loader::Outcome::LOADED);
  const auto name = saver_first->text;
  tick(3000, 86402);
  assert(saver_first->text == name && name == old_text);
}
void disable_and_wake() {
  start("slideshow", true);
  view_done(picture_loader::Outcome::LOADED);
  next("slideshow", false, "frame2");
  view_done(picture_loader::Outcome::LOADED);
  assert(!saver_first && !saver_second);
  const int before = created;
  tick(1000, 86400);
  assert(created == before);
  next("slideshow", true, "frame3");
  saver_tick(2000);
  assert(!saver_first && !saver_second);
  view_done(picture_loader::Outcome::LOADED);
  auto *first = saver_first;
  const auto text = first->text;
  saver_wake();
  assert(!camera_root && !saver_first && !saver_second);
  tick(3000, 86401);
  assert(first->text == text);
}
int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  if (scenario == "outage") outage();
  else if (scenario == "camera" || scenario == "media") transition(scenario);
  else { assert(scenario == "disable"); disable_and_wake(); }
}
'''


class NativeOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get('CXX', 'clang++')) or shutil.which('g++')
        if not compiler:
            raise unittest.SkipTest('no C++ compiler')
        runtime = (ROOT / 'components/smart_display/runtime_tiles.h').read_text()

        def function(name):
            start = runtime.index(f'inline void {name}(')
            # Skip forward declarations; each complete function ends at column zero.
            while runtime.index('\n', start) < runtime.index('{', start):
                start = runtime.index(f'inline void {name}(', start + 1)
            return runtime[start:runtime.index('\n}', start) + 2]

        def structure(name):
            start = runtime.index(f'struct {name} {{')
            return runtime[start:runtime.index('\n};', start) + 3]

        source = STUBS + structure('SaverChoice') + '\nSaverChoice saver_next, saver_now;\n'
        source += structure('ViewPicture') + '\nViewPicture camera;\n'
        source += '\n'.join(function(name) for name in (
            'saver_overlay_text', 'saver_words', 'saver_follow', 'saver_follow_pending',
            'view_done', 'camera_close', 'saver_hide', 'saver_wake', 'saver_tick'))
        cls.directory = ROOT / '.esphome/native-saver-overlay-tests'
        cls.directory.mkdir(parents=True, exist_ok=False)
        cls.addClassCleanup(shutil.rmtree, cls.directory)
        path = cls.directory / 'overlay.cpp'
        path.write_text(source + SCENARIOS)
        cls.binary = cls.directory / 'overlay'
        subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', f'-I{ROOT}',
                        str(path), '-o', str(cls.binary)], check=True)

    def test_existing_overlay_ticks_through_revision_failure_empty_and_retry(self):
        subprocess.run([str(self.binary), 'outage'], check=True)

    def test_camera_and_media_words_wait_for_their_replacement_picture(self):
        for kind in ('camera', 'media'):
            with self.subTest(kind=kind):
                subprocess.run([str(self.binary), kind], check=True)

    def test_overlay_creation_disable_and_wake_keep_the_existing_lifecycle(self):
        subprocess.run([str(self.binary), 'disable'], check=True)
