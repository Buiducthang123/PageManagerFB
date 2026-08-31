# Reels Publishing API



This document explains how to publish a reel on a Facebook Page.  To publish a reel on a Facebook Page, you will:

1. Initialize an upload session to upload your reel to the Meta servers
2. Upload your reel
2. Publish the reel to your Facebook Page

* Optionally, you can invite a collaborator to publish the reel on their Facebook Page.

#### Sharing Disclosure

When uploading a reel using your app, the app user should be presented with disclosure of and options for control over how their Reels are used on Facebook.

#### Privacy / Who can see this?

An app user should be able to select the audience for their reel. This selection corresponds to the privacy parameter in the publishing step. Publishing to a Page has implicit public scope, and only the 'Public' option should be available.

#### Limitations

* You can only publish Reels to Facebook Pages
* You can only crosspost Reels to Facebook Pages

#### Rate Limit

Reels API is limited to 30 API-published posts within a 24-hour moving period. This limit is enforced on the `POST /{page_id}/video_reels` endpoint when attempting to publish a reel. Meta recommends that your app also enforces the publishing rate limit, especially if your app allows app users to schedule posts to be published in the future.

*Sharing Disclosure Mockup*

## Before You Start

You will need:

* A Page access token requested from your app user who can perform the `CREATE_CONTENT` task on the Page
* The your app user must grant your app the following permissions using Facebook Login:
    * `pages_show_list`
    * `pages_read_engagement`
    * `pages_manage_posts`
* A video file that contains your reel

### Video Specifications {#requirements}

| Property | Specification |
| --- | --- |
| File Type | .mp4 (recommended) |
| Aspect Ratio | 9 x 16 |
| Resolution | 1080 x 1920 pixels (recommended). Minimum is 540 x 960 pixels |
| Frame Rate | 24 to 60 frames per second |
| Duration | 3 to 90 seconds. |
| Video Settings | * Chroma subsampling 4:2:0<br>* Closed GOP (2-5 seconds)<br>* Compression – H.264, H.265 (VP9, AV1 are also supported)<br><br>* Fixed frame rate<br>* Progressive scan |
| Audio Settings | * Audio bitrate – 128kbs+<br>* Channels – Stereo<br><br>* Codec – AAC Low Complexity<br>* Sample rate – 48kHz |

## Step 1: Initialize an Upload Session

Before you can publish a video to a Facebook Page, you must first upload it to the Meta social graph. You will need to initialize a video upload session to start the upload process. To start a session, send a `POST` request to the `/page-id/video_reels` endpoint, where ***page-id*** is the ID for your Facebook Page, with the `upload_phase` parameter set to `start`.

Be sure the host is ***`graph.facebook.com`***.

#### Example Request

_Formatted for readability. Replace ***bold***, ***italics values***, such as ***page_access_token***, with your values._

```curl
curl -X POST "https://graph.facebook.com/v25.0/Your_page_id/video_reels" \
     -H "Content-Type: application/json" \
     -d '{
           "upload_phase":"start",
           "access_token":"Your_page_access_token"
         }'
```

On success, your app will receive a video ID and a URL to the video. This video ID will be used in subsequent steps.

```json
{
  "video_id": "video-id",
  "upload_url": "https://rupload.facebook.com/video-upload/video-id",
}
```

## Step 2: Upload the Video {#upload}

Most Graph API calls use the graph.facebook.com host however, calls to upload videos for reels use ***`rupload.facebook.com`***.

The following file sources are supported for uploaded video files:

* A file located on your computer
* A file hosted on a public facing server, such as a CDN

### Upload a Local File

To initiate the upload of the video asset, send a `POST` request using application/octet-stream as content type to the `/video-upload/`***`video-id`*** endpoint where ***video-id*** is the ID from Step 1, `offset` is set to the first byte being upload, generally `0`, and `file_size` set to the size of your file, in bytes.

Be sure the host is ***`rupload.facebook.com`***.

| Header | Description |
| --- | --- |
| Authorization | Should contain “OAuth {access-token}”. |
| file_size | The total size in bytes of the video being uploaded. |
| file_url | The url for the video file hosted on a server. Supported protocols are http and https. Other protocols, and urls requiring authentication are not currently supported. |
| offset | The byte offset of the first byte being uploaded in this request.Generally should be set to 0, unless resuming an interrupted upload. If resuming an interrupted upload, set to the “offset” returned by /status. |
| place | ID of the page you want to tag as the location. **Note:** The page you specify must have a valid location associated with it. |

**Video Reel Upload Quick Reference**

#### Example Request

_Formatted for readability. Replace ***bold***, ***italics values***, such as ***page_access_token***, with your values._

```curl
curl -X POST "https://rupload.facebook.com/video-upload/v25.0/video-id" \
     -H "Authorization: OAuth Your_page_access_token" \
     -H "offset: 0" \
     -H "file_size: Your_file_size_in_bytes" \
     --data-binary "@my_video_file.mp4"
```

### Upload a Hosted File

To upload a hosted file, send a `POST` request to the `/video-upload/`***`video-id`*** endpoint where ***video-id*** is the ID returned in Step 1 and `file_url` is set to the URL for your hosted file.

Be sure the host is ***`rupload.facebook.com`***.

The API will now reject files hosted on sites that restrict access via robots.txt. Developers need to ensure that the hosting site allows the “facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)” user agent to fetch the hosted file.

Files hosted on Meta CDN (e.g.. fbcdn URLs) will get rejected. Instead, developers can use the crossposting feature to publish a video on multiple pages without uploading the video to each page. Refer to our [detailed guidance](https://developers.facebook.com/documentation/video-api/guides/crossposting) on crossposting.

#### Example Request

_Formatted for readability. Replace ***bold***, ***italics values***, such as ***page_access_token***, with your values._

```curl
curl -X POST "https://rupload.facebook.com/video-upload/v25.0/video_id" \
     -H "Authorization: OAuth Your_page_access_token" \
     -H "file_url: https://some.cdn.url/video.mp4"
```

### Example Upload Response

If you upload was successful, your app will receive a JSON object with `success` set to `true`.

```json
{"success": true}
```

### Get the Upload Status

To get the status of a video, send a `GET` request to the `/` `video-id` endpoint where the `video-id` is the ID from initialization step, and with `fields` set to `status`.

Be sure the host is `graph.facebook.com`.

#### Sample Request

```curl
curl -X GET "https://graph.facebook.com/v25.0/video-id?fields=status
&access_token=Your_page_access_token"
```

On success your app will receive a JSON object with status information that includes the processing, uploading, and publishing phases, and the video status.

| `status` Field Values | Description |
| --- | --- |
| `processing_phase` | This JSON object contains information about progress through the processing phase. This phase encompasses generating alternate media encodings, thumbnails, and other assets necessary for publishing. Values can include:<br>• `error` – This object will include a message about what went wrong during processing<br>• `status` – Values can be:<br><br>• `completed`<br>• `error`<br>• `not_started`<br>• `in_progress`<br><br> |
| `publishing_phase` | This JSON object contains information about progress through the publishing phase. This phase encompasses adding the video to the page, and if scheduled, will describe when the video is intended to be published. Values can include:<br><br>• `error` – This object will include a message about what went wrong during publishing<br>• `status` – Values can be:<br><br>• `completed`<br>• `error`<br>• `not_started`<br>• `in_progress`<br><br>• `publish_status` – Values can be:<br><br>• `draft`<br>• `error`<br>• `published`<br>• `scheduled`<br><br>• `publish_time` – A UNIX timestamp for the actual or scheduled published time<br> |
| `uploading_phase` | This JSON object contains information about progress through the uploading phase. Values can include:<br><br>• `bytes_transfered` – The number of bytes of the video file that have been uploaded. This field can be used as the `offset` value to resume an interrupted upload.<br>• `errors`<br>• `status` – Values can be:<br>• `completed`<br>• `error`<br>• `not_started`<br>• `in_progress`<br><br>• source_file_size<br> |
| `video_status` | The overall status of the video. Possible value can be:<br><br>* `error` – An error occurred during the processing or publishing phase<br>* `expired` – The video is expired and must be uploaded again<br>* `processing` – Meta is processing the video during or after upload<br>* `ready` – The video is ready to be published<br>* `uploading` – The video is currently uploading<br>* `upload_failed` – An error occured during upload phase, retry the upload             * `upload_complete` – The video has finished uploading. The uploaded bytes should equal the file size. |

**Video Status Quick Reference**

If re-uploading doesn’t work, try again from Step 1.

##### Example Response with a Processing Error

```json
{
  "status": {
    "video_status": "processing",
    "uploading_phase": {
      "status": "complete",
    },
    "processing_phase": {
      "status": "not_started",
      "error": {
        "message": "Resolution too low. Video must have a minimum resolution of 540p."
      }
    }
    "publishing_phase": {
      "status": "not_started",
    }
  }
}
```

##### Example Response for an Interrupted Upload

```json
{
  "status": {
    "video_status": "processing",
    "uploading_phase": {
      "status": "in_progress",
      "bytes_transfered": 50002
    }
    "processing_phase": {
      "status": "not_started"
    }
    "publishing_phase": {
      "status": "not_started",
    }
  }
}
```

| Error Type | Error Message | Recommended Solution |
| --- | --- | --- |
| OffsetInvalidError | Request starting offset is invalid | Set the ‘offset’ parameter to the `bytes_transfered` value returned in the Video endpoint `status` field |
| PartialRequestError | Partial request (did not match length of file) | Check the file size and try the upload again. |
| ProcessingFailedError | Request processing failed | Please try uploading again. Make sure the video meets all of the requirements. If the upload does not work, initialize a new upload session. |

### Resume an Interrupted Upload

If the video upload is interrupted, it can be resumed.

To resume an upload, send another POST request to the `/video-upload/video-id` endpoint and use the value for `upload_phase.bytes_transfered` as the value for `offset`.

## Step 3: Publish the Reel

To end the upload session and publish your video, send a `POST `request to the `/`***`page-id`***`/video_reels` endpoint. You can also include any of the additional fields, like `description`, which can include hashtags, and `title`.

Be sure the host is ***`graph.facebook.com`***.

| Field | Description |
| --- | --- |
| `video_id` | **Required**. The ID for the video as returned from initialize step. |
| `upload_phase` *enum{start,finish}* | **Required**. The upload status for the video file. To publish this should be `complete` |
| `video_state` *enum{DRAFT, SCHEDULED, PUBLISHED}* | **Required**. The publish status for the video. |
| `description  ` | The description for the video. Supports hashtags. |
| `title  ` | The title for the video |
| `scheduled_publish_time` | If the video is not being published immediately, set to a Unix timestamp integer for scheduled publish time.<br><br>If set, the publish time must be greater than 10 minutes from the current time and within 29 days of the current date, and `video_state` must be set to 'SCHEDULED'. |

**POST Page Video Reels Quick Reference**

#### Example Request

_Formatted for readability. Replace ***bold***, ***italics values***, such as ***page_access_token***, with your values._

```curl
curl -X POST "https://graph.facebook.com/v25.0/page-id/video_reels
    ?access_token=Your_page_access_token&video_id=video-id&upload_phase=finish
&video_state=PUBLISHED
&description=What a beautiful day! #sunnyand72"
```

On success your app will receive a JSON object with `success` set to `true`.

```json
{"success": true}
```

### Get a List of Reels

To get a list of all reels published on your Facebook Page, send a `GET` request to `/`***`page-id`***`/video_reels` endpoint.

Be sure the host is ***`graph.facebook.com`*** for this API call.

**Note:** When using `since` and `until` in your `GET` request, the date for `until` must be a date after the date for `since`. For example, if `since` is `2023-01-31`, `until` must be after `2023-01-31`. You can use both parameters, or one or the other. Date formats can be any of the following:

* `today`, `yesterday`
* Epoch timestamps (1676057525)
* `yyyy-mm-dd` (2023-1-31)

```curl
curl -X GET "https://graph.facebook.com/v25.0/page-id/video_reels?access_token=Your_page_access_token"
```

On success your app will receive a JSON object with information about your published reels such as video ID and published time.

```json
{
  "data": [
    {
      "updated_time": "unix_timestamp",
      "id": "video-1-id"
    },
    {
      "description": "sample_description",
      "updated_time": "unix_timestamp",
      "id": "video-2-id"
    },
    ...
  ]
}
```

## Invite a collaborator

Invite a person, a **collaborator**, to publish your reel on their Facebook Page.

To publish a reel on a collaborator's Facebook Page you will invite the collaborator to publish your reel on their Facebook Page. When they accept the invitation, the reel will immediately be published on their Facebook Page if the reel has been published, or the reel will be published on their Page when you publish the reel on your Page.

You will need:

* The ID for the collaborator's Facebook Page (for New Page Experience, use the delegate Page ID)
* The ID for the Video you want to share with a collaborator

#### Limitations

* You can only send 10 collaborator invitation per Page per 24 hours
* You can only publish Reels to other Facebook Pages

### Send an Invitation

To invite a collaborator to publish your reel on their Facebook Page, send a `POST` request to the `/`***`video-id`***`/collaborators` endpoint with the `target_id` parameter set to the ID for the collaborator's Facebook Page.

*Formatted for readability.*

```curl
curl -X POST "https://graph.facebook.com/v25.0/video-id/collaborators
  ?target_id=collaborators-page-id&access_token=your-page-access-token"
```

On success your app will receive a JSON response with the link to the invitation and your collaborator will receive an invite notification.

#### Example Response

```json
{
  "success": true,
  “collaborator_id”: “collaborators-page-id”
  “invitation_link”: “facebook-url-for-invitation”
}
```

### Get Invitation Status

To get the status for an invitation you sent, send a `GET` request to the `/`***`video-id`***`/collaborators` endpoint.

#### Example Request

*Formatted for readability.*

```curl
curl -X POST "https://graph.facebook.com/v25.0/video-id/collaborators
  ?access_token=your-page-or-user--access-token"
```

On success your app will receive a JSON response with the invitation status of `Accepted`, `Declined`, or `Pending`.

```json
{
  “id”: “video-id”
  “name”: “collaborators-page-name”
  “invite_status”: “Accepted”
  “invitation_link”: “facebook-url-for-invitation”
}
```

## Tag a location

To find a place to tag, you can use the [Pages Search API](https://developers.facebook.com/documentation/pages-api/search-pages). When searching for a place, only pages with valid locations can be used. When searching be sure to include the `location` field to verify this.

To tag the location in your reel, include the parameter `place` and the `id` returned from the Pages Search API in the publish call.

**Example Request**

```curl
curl -X POST "https://graph.facebook.com/v18.0/page-id/video_reels
    ?access_token=Your_page_access_token
    &video_id=video-id
    &upload_phase=finish
    &video_state=PUBLISHED
    &place=123456"
```

## Retrieve Copyright Information

At upload, a copyright check is run to see if your upload contains licensed content and lets you act appropriately before publishing. Videos in violation may have restricted access or be subject to monetization impacts. To retrieve the copyright check information on the Reel, you must have `pages_read_engagement` permission on the Page. You will need the ID of the Video and the Page access token. Please note that it may take a couple of minutes for the copyright check to be completed and return information.

### Limitations

* Works for Reels API uploads

* Only the owner can view the matches information

### Example

**Example Request**

```curl
curl -i -X GET "https://graph.facebook.com/v18.0/VIDEO_ID?fields=copyright_check_information&access_token=ACCESS_TOKEN"
```

**Example Responses**

*In progress:*

```json
{
  "copyright_check_information": {
        "status": {
            "status": "in_progress",
        },
    }
}
```

*Without any matches:*

```json
{
  "copyright_check_information": {
        "status": {
            "status": "complete",
            "matches_found": false
        },
    }
}
```

*With matches:*

```json
"copyright_check_information": {
        "status": {
            "status": "complete",
            "matches_found": true
        },
        "copyright_matches": [
            {
                "content_title": "Title 1",
                "owner_copyright_policy": {
                    "name": "Owner name 1",
                    "actions": [
                        {
                            "action": "TRACK",
                            "territories": "3",
                            "geos": [
                                "United Arab Emirates",
                                "Afghanistan",
                                "Antigua and Barbuda",
                            ]
                        }
                    ]
                },
                "matched_segments": [
                    {
                        "start_time_in_seconds": 90.5,
                        "duration_in_seconds": 35,
                        "segment_type": "AUDIO"
                    }
                ]
            },
            {
                "content_title": "Title 2",
                "owner_copyright_policy": {
                    "name": "Owner name 2",
                    "actions": [
                        {
                            "action": "BLOCK",
                            "territories": "1",
                            "geos": [
                                "Italy"
                            ]
                        }
                    ]
                },
                "matched_segments": [
                    {
                        "start_time_in_seconds": 90.5,
                        "duration_in_seconds": 35,
                        "segment_type": "AUDIO"
                    }
                ]
            }
        ]
    },
```

## Error Codes

Common error codes and possible mitigations.

| Error Code | Error Message | Possible Mitigation |
| --- | --- | --- |
| `100` | "error": {<br>"message": "(#100) Missing parameter: {a list of missing parameters}",<br>"type": "OAuthException",<br>"code": 100,<br>"fbtrace_id": "----"<br>}<br>} | A required parameter, such as `upload_phase`, is missing from your API call. Visit the endpoint reference to ensure all required parameters are included and be sure to check for typos. |
| `1363040` | The video you tried to upload has an aspect ratio that isn't supported on Facebook. Aspect ratios for videos need to be between 16x9 and 9x16. Please try uploading a video in a supported aspect ratio. | Aspect ratios for videos need to be between 16x9 and 9x16. |
| `1363127` | The video you tried to upload has resolution that isn't supported on Facebook for this product. Please try uploading a video with a supported resolution | Minimum resolution is 540 x 960 pixels. Recommended resolution is 1080 x 1920 pixels. |
| `1363128` | The video you tried to upload has a duration that isn't supported on Facebook for this product. Please try uploading a video with a supported duration. | Reels duration must be between 3 and 90 seconds. |
| `1363129` | The video you tried to upload has an average frame rate that isn't supported on Facebook for this product. Please try uploading a video with a supported frame rate | Reels frame rate must be between 24 and 60 frames per second. |

## Learn how to customize a reel

Customize your reel to create a richer reels experience for your viewers.

* [Add a custom cover photo for your reel](https://developers.facebook.com/docs/graph-api/reference/video/thumbnails#Creating)
* [Add your favorite music to your reel](https://developers.facebook.com/documentation/video-api/guides/music-recommendations#music-for-you)
* [Get music recommendations from Meta for new music](https://developers.facebook.com/documentation/video-api/guides/music-recommendations#new-music-on-facebook)
* [Get music recommendations from Meta for popular music on Facebook](https://developers.facebook.com/documentation/video-api/guides/music-recommendations#music-popular-on-facebook)

## See also

* [Get insights for your reels](https://developers.facebook.com/docs/graph-api/reference/video/video_insights#reels-metrics) to track engagement
- [Facebook Sharing to Reels From Android](https://developers.facebook.com/documentation/android/sharing-to-reels-facebook)
- [Facebook Sharing to Reels From iOS](https://developers.facebook.com/documentation/ios/sharing-to-reels-facebook)
* Check out the [Facebook Reels Publishing API Sample](https://github.com/fbsamples/reels_publishing_apis/tree/main/fb_reels_publishing_api_sample) available on Github.
* Check out the [Facebook Reels collection on the Postman API Platform](https://www.postman.com/meta/workspace/facebook/folder/23987686-8318a757-f788-40fa-a27b-e3dcfdc2f8d4?ctx=documentation&fbclid=IwAR23KQZpXD4mdQ-fFdEYU5KkigRM67AtsFPwJMJ-EdZZyuxAmuC-Zd22jV0)
* Check out the [Instagram Reels collection on the Postman API Platform](https://www.postman.com/meta/workspace/instagram/folder/23987686-91b95cb4-a10c-4aad-b15d-b54f2e6900e7?ctx=documentation&fbclid=IwAR31DFQLv9Uew9bxy-Cx6WqINMpg3sqdV-ipqVc2fBeFMdxVE4lOzDHGp80)
