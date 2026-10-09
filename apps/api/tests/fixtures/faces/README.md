# Face-detector test fixtures

640x480 JPEG renders of the interviewer avatar used by the web app
(`apps/web/public/avatars/source/mpfb.glb`), made headlessly with three.js in
Chromium: a simple room, key/fill lights, a webcam-like camera (55° field of
view). They exercise the server-side face detector (OpenCV YuNet) and the
static-face tracker in `tests/unit/integrity/test_faces.py` and the face-check
integration tests.

| File | Scene |
|---|---|
| `one_front.jpg` | one person, frontal, good light |
| `one_front_dark.jpg` | the same person with a darker skin tone (robustness across skin tones) |
| `one_low_light_dark.jpg` | darker skin tone, dim room |
| `one_yaw30.jpg` | head turned about 30 degrees |
| `one_edge.jpg` | face partly out of the frame (right edge) |
| `two_side_by_side.jpg`, `two_mixed_tones.jpg` | a second person next to / behind the first |
| `two_move_0..2.jpg` | the same two people, both moving slightly between frames |
| `none_empty_room.jpg` | nobody in view |
| `none_back_turned.jpg` | a person with their back to the camera (no face) |
| `poster_seq_0..3.jpg` | a framed print of a face on the wall (identical in every frame) while the live person moves slightly |
| `poster_only.jpg` | only the framed print |

Licence: the avatar is CC0 1.0 (created with Blender + MPFB/MakeHuman by the
TalkingHead project, https://github.com/met4citizen/TalkingHead/tree/main/avatars,
see `apps/web/public/avatars/LICENSES.md`); these renders are dedicated to the
public domain under CC0 1.0 as well. Darker skin tones are the same texture
multiplied by a brown tint. No real person appears in any fixture.
