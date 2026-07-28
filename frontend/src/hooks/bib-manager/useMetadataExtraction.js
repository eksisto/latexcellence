import { useState, useCallback, useRef } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { api } from "@/api/index";

export default function useMetadataExtraction({ formatResult }) {
  const { t } = useTranslation(["pages", "common"]);

  const [metadataText, setMetadataText] = useState("");
  const [metadataImages, setMetadataImages] = useState([]);
  const [metadataImagePreviews, setMetadataImagePreviews] = useState([]);
  const [metadataResult, setMetadataResult] = useState(null);
  const [editedMetadata, setEditedMetadata] = useState(null);
  const [missingFields, setMissingFields] = useState([]);
  const [step2Loading, setStep2Loading] = useState(false);
  const imageInputRef = useRef(null);

  const [doiInput, setDoiInput] = useState("");
  const [doiLoading, setDoiLoading] = useState(false);
  const [doiResult, setDoiResult] = useState(null);

  const handleExtractMetadata = useCallback(async () => {
    if (!metadataText.trim() && metadataImages.length === 0) {
      toast.error(t("pages:bibManager.step2NoInput"));
      return;
    }
    setStep2Loading(true);
    setMetadataResult(null);
    setEditedMetadata(null);
    setMissingFields([]);
    const formData = new FormData();
    if (metadataText.trim()) {
      formData.append("text", metadataText.trim());
    }
    for (const file of metadataImages) {
      formData.append("images", file);
    }
    if (formatResult) {
      formData.append("format_info", JSON.stringify(formatResult));
    }
    try {
      const data = await api.bibManager.extract(formData);

      if (data.missing_fields?.length > 0) {
        for (const field of data.missing_fields) {
          const fieldInfo = formatResult?.field_descriptions?.[field];
          const label = fieldInfo ? fieldInfo.split("\uFF08")[0] : field;
          toast.warning(
            t("pages:bibManager.step2MissingFieldWarning", { field: label }),
            { description: t("pages:bibManager.step2MissingFieldDesc") },
          );
        }
        setMissingFields(data.missing_fields);
      }

      setMetadataResult(data);
      setEditedMetadata({ ...data.fields });
      toast.success(t("pages:bibManager.step2Success"));
    } catch (e) {
      toast.error(t("pages:bibManager.step2Failed"), {
        description: e.message,
      });
    } finally {
      setStep2Loading(false);
    }
  }, [metadataText, metadataImages, formatResult, t]);

  const handleClearImage = useCallback((index) => {
    setMetadataImages((prev) => prev.filter((_, i) => i !== index));
    setMetadataImagePreviews((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleClearAllImages = useCallback(() => {
    setMetadataImages([]);
    setMetadataImagePreviews([]);
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }, []);

  const handleImageChange = useCallback((e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setMetadataImages((prev) => [...prev, ...files]);
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = () =>
        setMetadataImagePreviews((prev) => [...prev, reader.result]);
      reader.readAsDataURL(file);
    }
  }, []);

  const handleDoiLookup = useCallback(async () => {
    if (!doiInput.trim()) {
      toast.error(t("pages:bibManager.doiEmpty"));
      return;
    }
    setDoiLoading(true);
    setDoiResult(null);
    try {
      const data = await api.bibManager.doiLookup(doiInput.trim());
      setDoiResult(data);
      const fields = {};
      if (data.title) fields.title = data.title;
      if (data.authors?.length) fields.author = data.authors.join(" and ");
      if (data.year) fields.year = data.year;
      if (data.journal) fields.journal = data.journal;
      if (data.volume) fields.volume = data.volume;
      if (data.issue) fields.issue = data.issue;
      if (data.pages) fields.pages = data.pages;
      if (data.doi) fields.doi = data.doi;
      if (data.publisher) fields.publisher = data.publisher;
      if (data.abstract) fields.abstract = data.abstract;
      setEditedMetadata(fields);
      toast.success(t("pages:bibManager.doiLookupSuccess"));
    } catch (e) {
      toast.error(t("pages:bibManager.doiLookupFailed"), {
        description: e.message,
      });
    } finally {
      setDoiLoading(false);
    }
  }, [doiInput, t]);

  const handleMetadataEdit = useCallback((field, value) => {
    setEditedMetadata((prev) => ({ ...prev, [field]: value }));
  }, []);

  return {
    metadataText,
    setMetadataText,
    metadataImages,
    metadataImagePreviews,
    metadataResult,
    editedMetadata,
    setEditedMetadata,
    missingFields,
    step2Loading,
    imageInputRef,
    doiInput,
    setDoiInput,
    doiLoading,
    doiResult,
    handleExtractMetadata,
    handleClearImage,
    handleClearAllImages,
    handleImageChange,
    handleDoiLookup,
    handleMetadataEdit,
  };
}
